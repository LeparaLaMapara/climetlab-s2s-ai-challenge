#!/usr/bin/env python3

# make sure scipy is installed
import argparse

import climetlab as cml
import scipy  # noqa: F401
import xarray as xr

try:
    import logging

    import coloredlogs

    coloredlogs.install(level="DEBUG")
except ImportError:
    import logging


def main(args):
    if args.temperature:
        if args.test:
            build_temperature(args, inputyears="2010")
        else:
            build_temperature(args)
    if args.rain:
        if args.test:
            build_rain(args, inputyears="2010")
        else:
            build_rain(args)


global FINAL_FORMAT
FINAL_FORMAT = None

def get_final_format():
    global FINAL_FORMAT
    if FINAL_FORMAT:
        return FINAL_FORMAT
    is_test = "-dev"
    ds = cml.load_dataset(
        "s2s-ai-challenge-training-input" + is_test,
        origin="ecmwf",
        date=20200102,
        parameter="2t",
        format="netcdf",
    ).to_xarray()
    FINAL_FORMAT = ds.isel(forecast_time=0, realization=0, lead_time=0, drop=True)
    logging.info(f"target final coords : {FINAL_FORMAT.coords}")
    return FINAL_FORMAT

lm=46
leads = [pd.Timedelta(f'{d} d') for d in range(lm)]

start_year = 2000
reforecast_end_year = 2019

def create_valid_time_from_forecast_reference_time_and_lead_time(inits, leads):
    inits = xr.DataArray(inits, dims='forecast_reference_time', coords={'forecast_reference_time':inits})
    valid_times = xr.concat([xr.DataArray(inits + pd.Timedelta(f'{l} d'), dims='forecast_reference_time', coords={'forecast_reference_time': inits}) for l in range(lm)],'lead_time')
    valid_times = valid_times.assign_coords(lead_time=leads)
    return valid_times.rename('valid_time')

def forecast_valid_times():
    forecasts_inits = pd.date_range(start='2020-01-02', end='2020-12-31', freq='7D')
    return create_valid_time_from_forecast_reference_time_and_lead_time(forecasts_inits, leads)

def reforecast_valid_times():
    """Inits from year 2000 to 2019 for the same days as in 2020."""
    reforecasts_inits = []
    for year in range(start_year, reforecast_end_year+1):
        dates_year = pd.date_range(start=f'{year}-01-02', end=f'{year}-12-31', freq='7D')
        dates_year = xr.DataArray(dates_year, dims='forecast_reference_time', coords={'forecast_reference_time':dates_year})
        reforecasts_inits.append(dates_year)
    reforecasts_inits = xr.concat(reforecasts_inits, dim='forecast_reference_time')
    return create_valid_time_from_forecast_reference_time_and_lead_time(reforecasts_inits, leads)

def build_temperature(args, inputyears="*"):
    logging.info("Building temperature data")
    start_year = args.start_year
    outdir = args.outdir
    param = "t2m"

    # TODO
    # t2m:
    # long_name :
    #    2 metre temperature
    # units :
    #    K
    # standard_name :
    #    air_temperature

    # chunk_dim = "T"
    # chunk_dim = "X"
    # tmin = xr.open_dataset('http://iridl.ldeo.columbia.edu/SOURCES/.NOAA/.NCEP/.CPC/.temperature/.daily/.tmin/dods', chunks={chunk_dim:'auto'}) # noqa: E501
    # tmax = xr.open_dataset('http://iridl.ldeo.columbia.edu/SOURCES/.NOAA/.NCEP/.CPC/.temperature/.daily/.tmax/dods', chunks={chunk_dim:'auto'}) # noqa: E501

    tmin = xr.open_mfdataset(f"{args.input}/tmin/data.{inputyears}.nc").rename(
        {"tmin": "t"}
    )
    tmax = xr.open_mfdataset(f"{args.input}/tmax/data.{inputyears}.nc").rename(
        {"tmax": "t"}
    )
    t = xr.concat([tmin, tmax], "m").mean("m")

    t["T"] = xr.cftime_range(start="1979-01-01", freq="1D", periods=t.T.size)

    t = t.rename({"X": "longitude", "Y": "latitude", "T": "time"})
    t = t.sel(time=slice(f"{start_year-1}-12-24", None))

    t["t"].attrs = tmin["t"].attrs
    t["t"].attrs["long_name"] = "Daily Temperature" # check with EWC S2S
    # set standard_name for CF
    t = t.rename({"t": param})
    t = t + 273.15
    t[param].attrs["units"] = "K"
    t = t.interp_like(get_final_format())

    write_to_disk(ds=t, outdir=outdir, param=param, freq="daily", start_year=start_year)

    t["time"] = t["time"].compute()
    first_thursday = t.time.where(t.time.dt.dayofweek == 3, drop=True)[1]

    # forecasts issued every thursday: obs weekly aggregated from thursday->wednesday
    t = t.sel(time=slice(first_thursday, None)).resample(time="7D").mean()
    t = t.sel(time=slice(str(start_year), None)).chunk("auto")

    # takes an hour
    t.compute()
    
    # thats temperature with dimensions (time, longitude, latitude)
    t.to_netcdf(f"{outdir}/{param}_verification_weekly_since_{start_year}.nc")

    # but for the competition it would be best to have dims (forecast_reference_time, lead_time, longitude, latitude)
    t = t.rename({'time':'valid_time'}).sel(valid_time=forecast_valid_times)
    assert 'lead_time' in t.dims
    assert 'forecast_reference_time' in t.dims
    t.to_netcdf(f"{outdir}/{param}_verification_forecast_reference_time_2020_lead_time_weekly.nc")
    
    t = t.rename({'time':'valid_time'}).sel(valid_time=reforecast_valid_times)
    assert 'lead_time' in t.dims
    assert 'forecast_reference_time' in t.dims
    # likely to large for one file, split into many
    t.to_netcdf(f"{outdir}/{param}_verification_forecast_reference_time_{start_year}_{reforecast_end_year}_lead_time_weekly.nc")

def write_to_disk(ds, outdir, param, freq, start_year, netcdf=True, zarr=True):
    filename = f"{outdir}/{param}_verification_{freq}_since_{start_year}.nc"
    logging.info(f'Writing {param} in "{filename}"')
    ds.to_netcdf(filename)

    filename = f"{outdir}/{param}_verification_{freq}_since_{start_year}.zarr"
    logging.info(f'Writing {param} in "{filename}"')
    ds.chunk("auto").to_zarr(filename, consolidated=True, mode="w")


def build_rain(args, inputyears="*"):
    logging.info("Building rain data")
    start_year = args.start_year
    outdir = args.outdir
    param = "tp"  # TODO this is pr
    # rain = xr.open_dataset('http://iridl.ldeo.columbia.edu/SOURCES/.NOAA/.NCEP/.CPC/.UNIFIED_PRCP/.GAUGE_BASED/.GLOBAL/.v1p0/.extREALTIME/.rain/dods', chunks={'X':'auto'}) # noqa: E501
    # rain = xr.open_dataset('http://iridl.ldeo.columbia.edu/SOURCES/.NOAA/.NCEP/.CPC/.UNIFIED_PRCP/.GAUGE_BASED/.GLOBAL/.v1p0/.extREALTIME/.rain/dods', chunks={'T':'auto'}) # noqa: E501
    rain = xr.open_mfdataset(f"{args.input}/rain/data.{inputyears}.nc")
    rain = rain.rename({"X": "longitude", "Y": "latitude", "T": "time"})
    rain = rain.sel(time=slice(f"{start_year-1}-12-24", None))
    rain = rain.rename({"rain": param})

    rain = rain.interp_like(get_final_format())

    write_to_disk(
        ds=rain, outdir=outdir, param=param, freq="daily", start_year=start_year
    )

    rain["time"] = rain["time"].compute()
    first_thursday = rain.time.where(rain.time.dt.dayofweek == 3, drop=True)[
        1
    ].compute()

    # forecasts issued every thursday: obs weekly aggregated from thursday->wednesday
    rain = (
        rain.sel(time=slice(first_thursday, None))
        .resample(time="7D")
        .mean()
        .sel(time=slice(str(start_year), None))
        .chunk("auto")
    )

    # takes an hour
    write_to_disk(
        ds=rain, outdir=outdir, param=param, freq="weekly", start_year=start_year
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    # parser.add_argument("-p", "--param", nargs="+", help=' Either temperature or rain')
    parser.add_argument("-i", "--input", help="input netcdf files", default="/s2s-obs/")
    parser.add_argument(
        "-o",
        "--outdir",
        help="output netcdf and zarr files",
        default="/s2s-obs/forecast-benchmark",
    )
    parser.add_argument("--temperature", action="store_true")
    parser.add_argument("--rain", action="store_true")
    parser.add_argument(
        "--test",
        action="store_true",
        help="For dev purpose, use only part of the input data",
    )
    parser.add_argument("--start-year", type=int, default=2002)

    args = parser.parse_args()
    main(args)
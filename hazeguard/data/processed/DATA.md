# Data sources

Raw data is **not committed** (`data/raw/` is gitignored). Download everything below into `data/raw/` to recreate it.

Years used: **2015, 2019, 2022, 2023** (train on 2015, 2019, 2022 and 2023 up to mid-Sep; test on the 2023 haze, 25 Sep to 8 Oct 2023). Final choice depends on what the files actually contain, see the checks at the bottom.

## 1. Air quality (target variable)

| Source | What it is | Link |
| --- | --- | --- |
| Kaggle: Air Pollution Index (API) in Malaysia | Malaysian Air Pollutant Index, 2005 to present. 2005 to 2017 from data.gov.my, 2018 onwards from APIMS. Granularity and columns to be confirmed. | https://www.kaggle.com/datasets/ynshung/malaysia-air-pollution-index |
| Open-Meteo Air Quality API (fallback) | Modelled PM2.5 (`pm2_5`). Global data is about 45 km and 3-hourly, starting August 2022. Model output, not station measurements. | https://open-meteo.com/en/docs/air-quality-api |
| DOE APIMS (official, reference only) | Official live station readings. Mostly a viewer, not practical for bulk history. | https://eqms.doe.gov.my/APIMS/main |
| DOE API guide (PDF) | Official Air Pollutant Index definition and bands, use this to set the "unhealthy" threshold in `config.py`. | https://www.doe.gov.my/wp-content/uploads/2021/10/General-Information-of-Air-Pollutant-Index.pdf |

To confirm: if the Kaggle values are the API index (not PM2.5 in µg/m³), the 55 µg/m³ threshold does not apply. Set the threshold from the DOE guide and label results as "API index".

## 2. Fire hotspots

| Source | What it is | Link |
| --- | --- | --- |
| NASA FIRMS archive download | MODIS hotspots from Nov 2000, VIIRS S-NPP from Jan 2012. Request an archive for Sumatra, Kalimantan and Malaysia covering the years above. Delivered by email after login (Earthdata or emailed code). | https://firms.modaps.eosdis.nasa.gov/download/ |

Request this first, as it arrives by email.

## 3. Weather (wind, rain)

| Source | What it is | Link |
| --- | --- | --- |
| Open-Meteo Historical Weather API | Hourly wind speed, wind direction and rain, no API key needed. Fetched by `pipeline/physical/data/fetch.py`. | https://open-meteo.com/en/docs/historical-weather-api |

## 4. Optional (later project layers, not used for the physical model)

| Source | What it is | Link |
| --- | --- | --- |
| OpenDOSM air pollution | Monthly only, too coarse for 24/48/72h forecasting. | https://open.dosm.gov.my/data-catalogue/air_pollution |

## Where files go

```text
data/raw/        <- all downloads above (gitignored)
data/processed/  <- features.csv (written by the data person only)
data/out/        <- forecast.json (written by the models person only)
```

## Checks before using the air quality file

1. Both Aug to Oct 2015 and Aug to Oct 2019 have rows, with few gaps.
2. Station or city names match across the 2017/2018 boundary (the two source systems may spell them differently).
3. Columns and units are the same before and after 2018, and the values are the API index or PM2.5 (confirm which).
4. Data is hourly, or at least daily, throughout.

## Attribution

Air quality data: Department of Environment Malaysia (data.gov.my for 2005 to 2017, APIMS for 2018 onwards), via the Kaggle dataset by ynshung. Data from data.gov.my is licensed under Creative Commons Attribution; licence status of the APIMS-sourced years is unclear, so keep raw files out of the repo.

Fire data: NASA FIRMS. Weather and modelled air quality: Open-Meteo.

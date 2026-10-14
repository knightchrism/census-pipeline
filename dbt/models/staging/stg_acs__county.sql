-- Staging: typed, sentinel-cleaned county rows. 1 row = 1 county x 1 ACS vintage.
-- WHY: every downstream model gets clean numbers + consistent names regardless of year.
-- WHAT BREAKS if removed: intermediate/marts would have to re-implement casting & null rules.
with src as (select * from {{ source('census_raw', 'acs5_county') }})
select
    state || county                                  as geoid,
    state                                            as state_fips,
    county                                           as county_fips,
    -- 'Alachua County, Florida' -> 'Alachua County' (strip ", <State>" generically)
    trim(regexp_replace("NAME", ',\s*[^,]+$', ''))   as county_name,
    trim(regexp_extract("NAME", ',\s*([^,]+)$', 1))  as state_name,
    _year                                            as acs_year,
    (_year - 4) || '-' || _year                      as acs_period,
    {{ stg_acs_columns() }}
from src

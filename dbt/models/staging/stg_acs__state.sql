-- Staging: typed official state totals. 1 row = 1 state x 1 ACS vintage.
-- WHY: the official state estimate is the reconciliation target for county sums
-- and the only correct source for state-level medians (medians don't add up).
with src as (select * from {{ source('census_raw', 'acs5_state') }})
select
    state                         as state_fips,
    trim("NAME")                  as state_name,
    _year                         as acs_year,
    (_year - 4) || '-' || _year   as acs_period,
    {{ stg_acs_columns() }}
from src

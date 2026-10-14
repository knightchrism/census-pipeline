-- Fact: growth between every pair of loaded vintages, per county.
-- 1 row = county x (start_year, end_year). Growth is NULL-safe (see safe_growth macro).
-- CAVEAT: only compare non-overlapping 5-year windows (e.g. 2014 vs 2019 vs 2024);
-- overlapping windows share sample years, so their difference understates change.
-- Incomes are in each vintage's own nominal dollars (not inflation adjusted).
with f as (select * from {{ ref('fct_county_demographics') }})
select
    a.geoid || '-' || a.acs_year || '-' || b.acs_year as county_period_key,
    a.geoid, a.state_fips, b.county_name,
    a.acs_year as start_year, b.acs_year as end_year,
    (b.acs_year - a.acs_year) >= 5 as windows_non_overlapping,
    a.total_population as total_population_start, b.total_population as total_population_end,
    {{ safe_growth('b.total_population', 'a.total_population') }} as total_population_growth,
    a.median_household_income as median_household_income_start,
    b.median_household_income as median_household_income_end,
    {{ safe_growth('b.median_household_income', 'a.median_household_income') }} as median_household_income_growth_nominal,
    a.median_age as median_age_start, b.median_age as median_age_end,
    b.median_age - a.median_age as median_age_change,
    {{ safe_growth('b.pop_hispanic', 'a.pop_hispanic') }} as pop_hispanic_growth,
    b.pct_hispanic - a.pct_hispanic as pct_hispanic_change_pts
from f a
join f b on a.geoid = b.geoid and a.acs_year < b.acs_year

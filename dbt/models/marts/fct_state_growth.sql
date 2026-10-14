-- Fact: state-level growth between every pair of loaded vintages (official estimates).
select
    a.state_fips || '-' || a.acs_year || '-' || b.acs_year as state_period_key,
    a.state_fips, b.state_name, a.acs_year as start_year, b.acs_year as end_year,
    (b.acs_year - a.acs_year) >= 5 as windows_non_overlapping,
    {{ safe_growth('b.total_population', 'a.total_population') }} as total_population_growth,
    {{ safe_growth('b.median_household_income', 'a.median_household_income') }} as median_household_income_growth_nominal,
    b.median_age - a.median_age as median_age_change,
    {{ safe_growth('b.pop_hispanic', 'a.pop_hispanic') }} as pop_hispanic_growth
from {{ ref('fct_state_demographics') }} a
join {{ ref('fct_state_demographics') }} b on a.state_fips = b.state_fips and a.acs_year < b.acs_year

-- Fact: one row per state per vintage. Official state estimates (correct medians)
-- side by side with county rollups (for reconciliation tests).
-- WHAT BREAKS if removed: state-level metrics and the county-sum reconciliation test.
with counties as (
    select state_fips, acs_year,
           count(*)                    as county_count,
           sum(total_population)       as county_sum_total_population,
           sum(pop_hispanic)           as county_sum_pop_hispanic,
           sum(pop_white_non_hispanic) as county_sum_pop_white_non_hispanic,
           -- MOE of a sum, approximated as sqrt(sum(moe^2)) per Census guidance
           sqrt(sum(pop_hispanic_moe ^ 2)) as county_sum_pop_hispanic_moe_approx
    from {{ ref('fct_county_demographics') }}
    group by all
)
select
    s.state_fips || '-' || s.acs_year as state_year_key,
    s.state_fips, s.state_name, s.acs_year, s.acs_period,
    s.total_population, s.total_population_moe,
    s.median_household_income, s.median_household_income_moe,
    s.median_age, s.median_age_moe,
    s.pop_hispanic, s.pop_hispanic_moe,
    s.pop_white_non_hispanic, s.pop_black_non_hispanic, s.pop_asian_non_hispanic,
    s.pop_hispanic / nullif(s.total_population, 0) as pct_hispanic,
    c.county_count,
    c.county_sum_total_population, c.county_sum_pop_hispanic, c.county_sum_pop_white_non_hispanic,
    c.county_sum_pop_hispanic_moe_approx,
    s.loaded_at
from {{ ref('stg_acs__state') }} s
left join counties c using (state_fips, acs_year)

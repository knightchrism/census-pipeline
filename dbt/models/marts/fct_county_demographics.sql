-- Fact: one row per county per ACS vintage. The main table agents/analysts query.
-- WHAT BREAKS if removed: county metrics in metrics.yml and most MCP answers.
select
    geoid || '-' || acs_year as county_year_key,
    geoid, state_fips, county_fips, county_name, acs_year, acs_period,
    total_population, total_population_moe,
    median_household_income, median_household_income_moe, median_household_income_flag,
    median_household_income_cv,
    median_household_income_cv > 0.30 as median_household_income_low_reliability,
    median_age, median_age_moe, median_age_flag,
    pop_hispanic, pop_hispanic_moe,
    pop_white_non_hispanic, pop_white_non_hispanic_moe,
    pop_black_non_hispanic, pop_black_non_hispanic_moe,
    pop_asian_non_hispanic, pop_asian_non_hispanic_moe,
    pop_other_non_hispanic,
    pct_hispanic, pct_white_non_hispanic, pct_black_non_hispanic, pct_asian_non_hispanic,
    loaded_at
from {{ ref('int_acs__county_enriched') }}

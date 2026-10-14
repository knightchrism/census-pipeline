-- Intermediate: derived per-county columns (shares, reliability) kept out of staging
-- so staging stays a pure "clean the source" layer.
-- WHAT BREAKS if removed: fct_county_demographics loses shares and the CV reliability flag.
select
    s.*,
    -- Everyone not in the 4 selected groups (other races, two+ races, NH some other race).
    total_population - (pop_white_non_hispanic + pop_black_non_hispanic
                        + pop_asian_non_hispanic + pop_hispanic)          as pop_other_non_hispanic,
    pop_hispanic           / nullif(total_population, 0)                  as pct_hispanic,
    pop_white_non_hispanic / nullif(total_population, 0)                  as pct_white_non_hispanic,
    pop_black_non_hispanic / nullif(total_population, 0)                  as pct_black_non_hispanic,
    pop_asian_non_hispanic / nullif(total_population, 0)                  as pct_asian_non_hispanic,
    -- Coefficient of variation: ACS MOEs are 90% CIs, so SE = MOE / 1.645.
    -- CV > 0.30 is a common "use with caution" threshold.
    (median_household_income_moe / 1.645) / nullif(median_household_income, 0) as median_household_income_cv
from {{ ref('stg_acs__county') }} s

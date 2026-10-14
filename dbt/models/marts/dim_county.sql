-- Dimension: one row per county; names come from the latest vintage.
-- WHY: a stable county list for relationships tests and lookups.
-- WHAT BREAKS if removed: relationships tests and the MCP county filter validation.
select
    geoid, state_fips, county_fips, county_name, state_name,
    min(acs_year) over (partition by geoid) as first_acs_year,
    max(acs_year) over (partition by geoid) as last_acs_year
from {{ ref('stg_acs__county') }}
qualify row_number() over (partition by geoid order by acs_year desc) = 1

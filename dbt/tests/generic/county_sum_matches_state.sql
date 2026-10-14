{# Generic test on a state-level model: sum of counties must match the official
   state estimate within a relative tolerance.
   WHY: proves no counties were dropped/duplicated between API and marts.
   For additive counts (population, Hispanic population) ACS county estimates
   sum exactly to the state; medians are NOT additive and must not use this test. #}
{% test county_sum_matches_state(model, column_name, county_sum_column, tolerance=none) %}
{% set tol = tolerance if tolerance is not none else var('county_state_tolerance') %}
select state_fips, acs_year, {{ column_name }} as state_value, {{ county_sum_column }} as county_sum
from {{ model }}
where {{ column_name }} is null
   or {{ county_sum_column }} is null
   or abs({{ county_sum_column }} - {{ column_name }}) > {{ tol }} * {{ column_name }}
{% endtest %}

{#
  Census sentinel / annotation handling.
  WHY: the API encodes "no value" as huge negative numbers. If they are not
  nulled, a single -666666666 income makes every average wildly negative.
  WHAT BREAKS if removed: medians/sums silently include garbage values.

  Codes (https://www.census.gov/data/developers/data-sets/acs-1year/notes-on-acs-estimate-and-annotation-values.html):
   -666666666  estimate could not be computed (too few sample observations)
   -999999999  too few sample observations to compute estimate/MOE
   -888888888  not applicable / not available
   -555555555  estimate is controlled; MOE not appropriate (treat MOE as 0-ish)
   -333333333  median in open-ended interval; MOE not appropriate
   -222222222  MOE could not be computed (too few sample observations)
#}

{% macro acs_sentinels() -%}
(-666666666, -999999999, -888888888, -555555555, -333333333, -222222222)
{%- endmacro %}

{# numeric value with sentinels turned into NULL #}
{% macro acs_clean(col) -%}
case when try_cast({{ col }} as double) in {{ acs_sentinels() }} then null
     else try_cast({{ col }} as double) end
{%- endmacro %}

{# the sentinel code itself (NULL when the value is a real number) so nothing is lost #}
{% macro acs_flag(col) -%}
case when try_cast({{ col }} as double) in {{ acs_sentinels() }}
     then cast(try_cast({{ col }} as double) as bigint) end
{%- endmacro %}

{# growth = (new - old) / old, null-safe: NULL if either side is NULL or old = 0 #}
{% macro safe_growth(new, old) -%}
case when {{ old }} is null or {{ new }} is null or {{ old }} = 0 then null
     else ({{ new }} - {{ old }}) / {{ old }}::double end
{%- endmacro %}

{# Shared staging SELECT for county and state raw tables #}
{% macro stg_acs_columns() -%}
    {%- for code, name in var('acs_variables').items() %}
    {{ acs_clean('"' ~ code ~ 'E"') }} as {{ name }},
    {{ acs_clean('"' ~ code ~ 'M"') }} as {{ name }}_moe,
    {{ acs_flag('"' ~ code ~ 'E"') }}  as {{ name }}_flag,
    {{ acs_flag('"' ~ code ~ 'M"') }}  as {{ name }}_moe_flag,
    {%- endfor %}
    _dataset   as source_dataset,
    _load_id   as load_id,
    _loaded_at as loaded_at
{%- endmacro %}

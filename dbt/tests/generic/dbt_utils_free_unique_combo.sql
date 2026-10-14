{# Generic test: the combination of columns is unique (grain check) without needing
   the dbt_utils package (one less dependency for a learner to install). #}
{% test dbt_utils_free_unique_combo(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}

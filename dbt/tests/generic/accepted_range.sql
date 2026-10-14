{# Generic test: values must fall within [min_value, max_value]; NULLs pass.
   WHY: catches unit/sentinel bugs (e.g. negative income) that not_null can't. #}
{% test accepted_range(model, column_name, min_value=none, max_value=none) %}
select {{ column_name }} as bad_value
from {{ model }}
where {{ column_name }} is not null
  and (
    {% if min_value is not none %} {{ column_name }} < {{ min_value }} {% else %} false {% endif %}
    or
    {% if max_value is not none %} {{ column_name }} > {{ max_value }} {% else %} false {% endif %}
  )
{% endtest %}

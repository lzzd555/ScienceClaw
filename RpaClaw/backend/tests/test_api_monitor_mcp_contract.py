from backend.rpa.api_monitor_mcp_contract import parse_api_monitor_tool_yaml


def test_parse_get_tool_yaml_builds_contract():
    contract = parse_api_monitor_tool_yaml(
        """
name: search_orders
description: Search orders by keyword and status
method: GET
url: /api/orders
parameters:
  type: object
  properties:
    keyword:
      type: string
      description: Order id, phone, or username
    status:
      type: string
      description: Order status
request:
  query:
    keyword: "{{ keyword }}"
    status: "{{ status }}"
response:
  type: object
"""
    )

    assert contract.valid is True
    assert contract.name == "search_orders"
    assert contract.method == "GET"
    assert contract.url == "/api/orders"
    assert contract.input_schema["properties"]["keyword"]["type"] == "string"
    assert contract.query_mapping == {"keyword": "{{ keyword }}", "status": "{{ status }}"}
    assert contract.body_mapping == {}
    assert contract.validation_errors == []


def test_parse_post_tool_yaml_builds_body_contract():
    contract = parse_api_monitor_tool_yaml(
        """
name: create_user
description: Create a user
method: POST
url: /api/users
parameters:
  type: object
  properties:
    name:
      type: string
    email:
      type: string
request:
  body:
    name: "{{ name }}"
    email: "{{ email }}"
response:
  type: object
"""
    )

    assert contract.valid is True
    assert contract.name == "create_user"
    assert contract.method == "POST"
    assert contract.body_mapping == {"name": "{{ name }}", "email": "{{ email }}"}
    assert contract.response_schema == {"type": "object"}


def test_invalid_yaml_reports_parse_error():
    contract = parse_api_monitor_tool_yaml("name: [broken")

    assert contract.valid is False
    assert contract.name == ""
    assert any("YAML" in error for error in contract.validation_errors)


def test_missing_required_fields_are_invalid():
    contract = parse_api_monitor_tool_yaml(
        """
name: ""
description: Missing request shape
parameters:
  type: object
  properties: {}
"""
    )

    assert contract.valid is False
    assert "name is required" in contract.validation_errors
    assert "method is required" in contract.validation_errors
    assert "url is required" in contract.validation_errors


def test_mapping_unknown_parameter_is_invalid():
    contract = parse_api_monitor_tool_yaml(
        """
name: search_orders
description: Search orders
method: GET
url: /api/orders
parameters:
  type: object
  properties:
    keyword:
      type: string
request:
  query:
    keyword: "{{ keyword }}"
    status: "{{ status }}"
"""
    )

    assert contract.valid is False
    assert "request.query.status references unknown parameter 'status'" in contract.validation_errors


def test_tool_name_must_be_mcp_safe():
    contract = parse_api_monitor_tool_yaml(
        """
name: search orders
description: Search orders
method: GET
url: /api/orders
parameters:
  type: object
  properties: {}
"""
    )

    assert contract.valid is False
    assert "name must match ^[A-Za-z_][A-Za-z0-9_]*$" in contract.validation_errors

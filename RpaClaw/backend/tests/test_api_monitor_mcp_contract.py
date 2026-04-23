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


def test_contract_document_uses_singular_header_mapping_key():
    contract = parse_api_monitor_tool_yaml(
        """
name: search_orders
description: Search orders by keyword
method: GET
url: /api/orders/{tenant_id}
parameters:
  type: object
  properties:
    tenant_id:
      type: string
    keyword:
      type: string
request:
  path:
    tenant_id: "{{ tenant_id }}"
  query:
    keyword: "{{ keyword }}"
  headers:
    X-Tenant-ID: "{{ tenant_id }}"
response:
  type: object
"""
    )

    document = contract.to_document()

    assert document["header_mapping"] == {"X-Tenant-ID": "{{ tenant_id }}"}
    assert "headers_mapping" not in document


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


def test_non_object_yaml_root_is_invalid_and_preserves_definition():
    yaml_definition = "- name: search_orders"

    contract = parse_api_monitor_tool_yaml(yaml_definition)

    assert contract.valid is False
    assert contract.yaml_definition == yaml_definition
    assert contract.raw_definition == [{"name": "search_orders"}]
    assert contract.validation_errors == ["YAML root must be an object"]


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


def test_invalid_request_section_shape_is_invalid():
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
request: []
"""
    )

    assert contract.valid is False
    assert "request must be an object" in contract.validation_errors


def test_invalid_request_query_shape_is_invalid():
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
  query: x
"""
    )

    assert contract.valid is False
    assert "request.query must be an object" in contract.validation_errors


def test_invalid_response_shape_is_invalid():
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
response: []
"""
    )

    assert contract.valid is False
    assert "response must be an object" in contract.validation_errors


def test_invalid_method_is_rejected():
    contract = parse_api_monitor_tool_yaml(
        """
name: search_orders
description: Search orders
method: TRACE
url: /api/orders
parameters:
  type: object
  properties: {}
"""
    )

    assert contract.valid is False
    assert "method must be one of GET, POST, PUT, PATCH, DELETE" in contract.validation_errors


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


def test_nested_mapping_validation_is_deterministic():
    contract = parse_api_monitor_tool_yaml(
        """
name: update_profile
description: Update profile
method: POST
url: /api/profile
parameters:
  type: object
  properties: {}
request:
  body:
    search: "{{ status }} and {{ keyword }}"
    nested:
      value: "{{ keyword }}"
      label: "{{ status }}"
"""
    )

    assert contract.valid is False
    assert contract.validation_errors == [
        "request.body.search references unknown parameter 'status'",
        "request.body.search references unknown parameter 'keyword'",
        "request.body.nested.value references unknown parameter 'keyword'",
        "request.body.nested.label references unknown parameter 'status'",
    ]


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

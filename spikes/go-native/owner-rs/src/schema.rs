//! Small language-neutral JSON Schema subset for isolated input/output evidence.
//! Not the fixed runtime-wire schema interpreter; unsupported documents reject.
use serde_json::Value;

const TYPES: [&str; 7] = [
    "object", "array", "string", "boolean", "null", "integer", "number",
];

pub(crate) fn matches(schema: &Value, value: &Value) -> bool {
    supported(schema, 0) && check(schema, value)
}

fn supported(schema: &Value, depth: usize) -> bool {
    if depth > 64 {
        return false;
    }
    let Some(object) = schema.as_object() else {
        return false;
    };
    if object.keys().any(|key| {
        !matches!(
            key.as_str(),
            "$schema" | "type" | "properties" | "required" | "additionalProperties" | "items"
        )
    }) {
        return false;
    }
    if let Some(dialect) = schema.get("$schema")
        && dialect != "https://json-schema.org/draft/2020-12/schema"
    {
        return false;
    }
    if let Some(kind) = schema.get("type") {
        match kind {
            Value::String(kind) if TYPES.contains(&kind.as_str()) => {}
            Value::Array(types) if !types.is_empty() => {
                for (index, kind) in types.iter().enumerate() {
                    if !kind.as_str().is_some_and(|kind| TYPES.contains(&kind))
                        || types[..index].contains(kind)
                    {
                        return false;
                    }
                }
            }
            _ => return false,
        }
    }
    if let Some(properties) = schema.get("properties") {
        let Some(properties) = properties.as_object() else {
            return false;
        };
        if properties
            .values()
            .any(|child| !supported(child, depth + 1))
        {
            return false;
        }
    }
    if let Some(required) = schema.get("required") {
        let Some(required) = required.as_array() else {
            return false;
        };
        if required
            .iter()
            .enumerate()
            .any(|(i, key)| !key.is_string() || required[..i].contains(key))
        {
            return false;
        }
    }
    if let Some(additional) = schema.get("additionalProperties")
        && !additional.is_boolean()
    {
        return false;
    }
    schema
        .get("items")
        .is_none_or(|items| supported(items, depth + 1))
}

fn kind_matches(kind: &str, value: &Value) -> bool {
    match kind {
        "object" => value.is_object(),
        "array" => value.is_array(),
        "string" => value.is_string(),
        "boolean" => value.is_boolean(),
        "null" => value.is_null(),
        "integer" => value.as_f64().is_some_and(|n| n.fract() == 0.0),
        "number" => value.is_number(),
        _ => false,
    }
}

fn check(schema: &Value, value: &Value) -> bool {
    if let Some(kind) = schema.get("type") {
        let accepted = match kind {
            Value::String(kind) => kind_matches(kind, value),
            Value::Array(types) => types
                .iter()
                .any(|kind| kind.as_str().is_some_and(|kind| kind_matches(kind, value))),
            _ => false,
        };
        if !accepted {
            return false;
        }
    }
    if let Some(fields) = value.as_object() {
        if schema
            .get("required")
            .and_then(Value::as_array)
            .is_some_and(|required| {
                required
                    .iter()
                    .any(|key| !key.as_str().is_some_and(|key| fields.contains_key(key)))
            })
        {
            return false;
        }
        for (name, child) in fields {
            if let Some(property) = schema
                .get("properties")
                .and_then(|properties| properties.get(name))
            {
                if !check(property, child) {
                    return false;
                }
            } else if schema["additionalProperties"] == false {
                return false;
            }
        }
    }
    if let Some(items) = value.as_array()
        && let Some(item_schema) = schema.get("items")
        && items.iter().any(|item| !check(item_schema, item))
    {
        return false;
    }
    true
}

#[cfg(test)]
mod tests {
    use super::matches;
    use serde_json::json;

    #[test]
    fn nullable_arrays_validate_each_item_and_reject_wrong_types() {
        let schema = json!({"type":["array","null"],"items":{"type":"string"}});
        assert!(matches(&schema, &json!(["name"])));
        assert!(matches(&schema, &json!(null)));
        assert!(!matches(&schema, &json!("name")));
        assert!(!matches(&schema, &json!([false])));
    }

    #[test]
    fn unsupported_constraints_reject_even_in_an_absent_optional_property() {
        let schema =
            json!({"type":"object","properties":{"optional":{"type":"string","pattern":".*"}}});
        assert!(!matches(&schema, &json!({})));
        assert!(!matches(&json!({"type":"future"}), &json!(null)));
        assert!(!matches(&json!({"type":[]}), &json!(null)));
    }

    #[test]
    fn required_fields_and_closed_objects_are_enforced() {
        let schema = json!({"type":"object","required":["ready"],"additionalProperties":false,"properties":{"ready":{"type":"boolean"}}});
        assert!(matches(&schema, &json!({"ready":true})));
        assert!(!matches(&schema, &json!({})));
        assert!(!matches(&schema, &json!({"ready":true,"extra":0})));
        assert!(!matches(&schema, &json!({"ready":"true"})));
    }
}

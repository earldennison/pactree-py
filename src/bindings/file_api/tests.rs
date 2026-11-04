use super::*;
use serde_json::Value;

#[test]
fn selects_pact_tree_through_canonical_crate() {
    let pact = r#"{
      "id": "root",
      "componentType": "root",
      "children": [
        {"id": "active", "componentType": "message_turn", "pact_depth": 0, "role": "user"}
      ]
    }"#;

    let selected = select_pact_tree_json(pact, "d0").expect("selects active turn");
    let value: Value = serde_json::from_str(&selected).expect("json output");
    assert_eq!(value[0]["id"], "active");
}

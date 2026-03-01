import os
import re
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, abort, flash, render_template, request, session

from app.services.authz import is_admin_user

admin_tools_bp = Blueprint("admin_tools", __name__, url_prefix="/admin")


def _session_user_sub() -> str:
    u = session.get("user")
    if isinstance(u, dict):
        return str(u.get("sub") or "").strip()
    return str(session.get("sub") or "").strip()


def _require_allowed_admin_user() -> None:
    """Hard gate: only users in the configured Cognito admin group may access.

    Use 404 to avoid revealing the page exists.
    """
    if not is_admin_user():
        abort(404)


def _boto3_session():
    import boto3  # boto3 is already in requirements.txt

    region = (os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "eu-north-1").strip()

    access_key = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("ACCESS_KEY")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("SECRET_KEY")
    session_token = os.getenv("AWS_SESSION_TOKEN")

    if access_key and secret_key:
        return boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            aws_session_token=session_token,
            region_name=region,
        )

    # Fall back to the normal boto3 credential chain (IAM role, etc.)
    return boto3.Session(region_name=region)


def _ddb_client():
    return _boto3_session().client("dynamodb")


def _list_tables(client) -> List[str]:
    tables: List[str] = []
    start: Optional[str] = None
    while True:
        if start:
            resp = client.list_tables(ExclusiveStartTableName=start)
        else:
            resp = client.list_tables()
        tables.extend(resp.get("TableNames", []))
        start = resp.get("LastEvaluatedTableName")
        if not start:
            break
    return sorted(set(tables), key=str.lower)


def _describe_table(client, table_name: str) -> Dict[str, Any]:
    return client.describe_table(TableName=table_name)["Table"]


def _key_fields(table_desc: Dict[str, Any]) -> List[str]:
    return [k["AttributeName"] for k in table_desc.get("KeySchema", [])]


def _key_attr_types(table_desc: Dict[str, Any]) -> Dict[str, str]:
    defs = table_desc.get("AttributeDefinitions", [])
    return {d["AttributeName"]: d["AttributeType"] for d in defs}


def _discover_candidate_user_fields(client, table_name: str, sample_limit: int = 50) -> List[str]:
    """
    DynamoDB doesn't have schema for non-key attributes.
    We scan a small sample and list likely user-id-like attribute names.
    """
    # Always include common names
    candidates = {
        "userId",
        "user_id",
        "ownerId",
        "owner_id",
        "sub",
        "cognitoSub",
        "cognito_sub",
        "userid",
        "ownerid",
    }

    scanned = 0
    start_key = None
    name_re = re.compile(r"(user|owner).*(id)|(^sub$)|cognito", re.IGNORECASE)

    while scanned < sample_limit:
        kwargs: Dict[str, Any] = {"TableName": table_name, "Limit": min(25, sample_limit - scanned)}
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key
        resp = client.scan(**kwargs)
        items = resp.get("Items", [])
        for it in items:
            for k in it.keys():
                if name_re.search(k):
                    candidates.add(k)
        scanned += len(items)
        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break

    return sorted(candidates, key=str.lower)


def _scan_matching_keys(
    client,
    table_name: str,
    table_desc: Dict[str, Any],
    field_name: str,
    old_value: str,
    max_items: int,
) -> List[Dict[str, Any]]:
    key_fields = _key_fields(table_desc)

    # Only project keys (+ field if it's not already a key) to keep payload small.
    # If the field is also part of the key, including it twice can cause
    # "Two document paths overlap" ProjectionExpression errors.
    projection_fields: List[str] = list(key_fields)
    if field_name and field_name not in projection_fields:
        projection_fields.append(field_name)

    expr_names: Dict[str, str] = {"#f": field_name}
    projection_aliases: List[str] = []
    for i, name in enumerate(projection_fields):
        alias = f"#p{i}"
        expr_names[alias] = name
        projection_aliases.append(alias)

    start_key = None
    keys: List[Dict[str, Any]] = []

    while True:
        kwargs: Dict[str, Any] = {
            "TableName": table_name,
            "FilterExpression": "#f = :old",
            "ExpressionAttributeNames": expr_names,
            "ExpressionAttributeValues": {":old": {"S": old_value}},
            "ProjectionExpression": ", ".join(projection_aliases),
            "Limit": 250,
        }
        if start_key:
            kwargs["ExclusiveStartKey"] = start_key

        resp = client.scan(**kwargs)
        for item in resp.get("Items", []):
            key = {k: item[k] for k in key_fields if k in item}
            if len(key) == len(key_fields):
                keys.append(key)
            if len(keys) >= max_items:
                return keys

        start_key = resp.get("LastEvaluatedKey")
        if not start_key:
            break

    return keys


def _update_non_key_field(client, table_name: str, key: Dict[str, Any], field_name: str, new_value: str) -> None:
    client.update_item(
        TableName=table_name,
        Key=key,
        UpdateExpression="SET #f = :new",
        ExpressionAttributeNames={"#f": field_name},
        ExpressionAttributeValues={":new": {"S": new_value}},
    )


def _attrval_to_str(av: Dict[str, Any]) -> str:
    if "S" in av:
        return av["S"]
    if "N" in av:
        return av["N"]
    if "B" in av:
        return "<binary>"
    return str(av)


def _put_attrval(value: str, attr_type: str) -> Dict[str, Any]:
    if attr_type == "N":
        return {"N": value}
    if attr_type == "B":
        # Not supported for user ids in this tool
        return {"S": value}
    return {"S": value}


def _copy_delete_rekey(
    client,
    table_name: str,
    table_desc: Dict[str, Any],
    key: Dict[str, Any],
    key_field_to_change: str,
    new_value: str,
) -> None:
    resp = client.get_item(TableName=table_name, Key=key)
    item = resp.get("Item")
    if not item:
        return

    key_types = _key_attr_types(table_desc)
    attr_type = key_types.get(key_field_to_change, "S")

    new_item = dict(item)
    new_item[key_field_to_change] = _put_attrval(new_value, attr_type)

    client.put_item(TableName=table_name, Item=new_item)
    client.delete_item(TableName=table_name, Key=key)


@admin_tools_bp.route("/userid-migrate", methods=["GET", "POST"])
def userid_migrate():
    _require_allowed_admin_user()

    client = _ddb_client()
    tables = _list_tables(client)

    selected_table = (request.values.get("table") or (tables[0] if tables else "")).strip()
    selected_field = (request.values.get("field") or "userId").strip()
    old_user_id = (request.values.get("old_user_id") or "").strip()
    new_user_id = (request.values.get("new_user_id") or "").strip()
    mode = (request.values.get("mode") or "preview").strip().lower()  # preview | apply

    try:
        max_items = int(request.values.get("max_items") or "2000")
    except Exception:
        max_items = 2000

    max_items = max(1, min(max_items, 20000))

    table_desc: Optional[Dict[str, Any]] = None
    key_fields: List[str] = []
    candidate_fields: List[str] = []
    field_is_key = False

    if selected_table:
        table_desc = _describe_table(client, selected_table)
        key_fields = _key_fields(table_desc)
        field_is_key = selected_field in set(key_fields)
        candidate_fields = _discover_candidate_user_fields(client, selected_table)

    matches: List[Dict[str, Any]] = []
    sample_keys: List[str] = []
    changed = 0

    if request.method == "POST":
        if not selected_table:
            flash("No table selected.", "danger")
        elif not selected_field:
            flash("UserId field is required.", "danger")
        elif not old_user_id or not new_user_id:
            flash("Old and new user id are required.", "danger")
        elif old_user_id == new_user_id:
            flash("Old and new user id are the same.", "danger")
        elif not table_desc:
            flash("Table could not be described.", "danger")
        else:
            matches = _scan_matching_keys(
                client=client,
                table_name=selected_table,
                table_desc=table_desc,
                field_name=selected_field,
                old_value=old_user_id,
                max_items=max_items,
            )

            sample_keys = [
                ", ".join([f"{k}={_attrval_to_str(v)}" for k, v in key.items()]) for key in matches[:10]
            ]

            if mode == "apply":
                if field_is_key:
                    for key in matches:
                        _copy_delete_rekey(
                            client=client,
                            table_name=selected_table,
                            table_desc=table_desc,
                            key=key,
                            key_field_to_change=selected_field,
                            new_value=new_user_id,
                        )
                        changed += 1
                else:
                    for key in matches:
                        _update_non_key_field(
                            client=client,
                            table_name=selected_table,
                            key=key,
                            field_name=selected_field,
                            new_value=new_user_id,
                        )
                        changed += 1

                flash(f"Updated {changed} item(s) in {selected_table}.", "success")
            else:
                flash(f"Preview: {len(matches)} item(s) match in {selected_table}.", "info")

    return render_template(
        "admin_tools.html",
        tables=tables,
        selected_table=selected_table,
        candidate_fields=candidate_fields,
        selected_field=selected_field,
        key_fields=key_fields,
        field_is_key=field_is_key,
        old_user_id=old_user_id,
        new_user_id=new_user_id,
        max_items=max_items,
        sample_keys=sample_keys,
        changed=changed,
    )

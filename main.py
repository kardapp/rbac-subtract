import kopf
import kubernetes
from kubernetes import client

from subtract import subtract

_skip_annotation_prefixes = ('kopf.zalando.org/', 'kubectl.kubernetes.io/')


@kopf.on.create('kim.karolinska.se', 'v1', 'modifyclusterroles')
@kopf.on.update('kim.karolinska.se', 'v1', 'modifyclusterroles')
@kopf.on.resume('kim.karolinska.se', 'v1', 'modifyclusterroles')
def handle_modify_cluster_role(spec, name, meta, uid, logger, **kwargs):
    source_name = spec.get('clusterRole')
    remove_rules = spec.get('removeRules', [])

    api = client.RbacAuthorizationV1Api()

    logger.info(f"Reading source ClusterRole '{source_name}'...")
    try:
        source_role = api.read_cluster_role(name=source_name)
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:
            raise kopf.PermanentError(
                f"Source ClusterRole '{source_name}' not found"
            )
        raise

    serialized = client.ApiClient().sanitize_for_serialization(source_role)
    source_rules = serialized.get('rules', [])
    aggregate = serialized.get('aggregationRule')

    logger.info(
        f"Subtracting {len(remove_rules)} remove rule(s) from "
        f"{len(source_rules)} source rule(s)..."
    )
    try:
        result_rules = subtract(source_rules, remove_rules, logger=logger)
    except ValueError as e:
        raise kopf.PermanentError(str(e))

    labels = dict(meta.get('labels') or {})
    labels['app.kubernetes.io/managed-by'] = 'rbac-subtract'

    annotations = {
        k: v for k, v in (meta.get('annotations') or {}).items()
        if not k.startswith(_skip_annotation_prefixes)
    }

    owner_ref = client.V1OwnerReference(
        api_version='kim.karolinska.se/v1',
        kind='ModifyClusterRole',
        name=name,
        uid=uid,
        controller=True,
        block_owner_deletion=True,
    )

    target = client.V1ClusterRole(
        metadata=client.V1ObjectMeta(
            name=name,
            labels=labels,
            annotations=annotations,
            owner_references=[owner_ref],
        ),
        rules=result_rules,
        aggregation_rule=aggregate,
    )

    try:
        existing = api.read_cluster_role(name=name)
        target.metadata.resource_version = existing.metadata.resource_version
        api.replace_cluster_role(name=name, body=target)
        logger.info(f"Updated ClusterRole '{name}' with {len(result_rules)} rule(s)")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:
            api.create_cluster_role(body=target)
            logger.info(f"Created ClusterRole '{name}' with {len(result_rules)} rule(s)")
        else:
            raise

    return {'status': 'ok', 'rulesCount': len(result_rules)}


@kopf.on.delete('kim.karolinska.se', 'v1', 'modifyclusterroles')
def handle_delete(name, logger, **kwargs):
    api = client.RbacAuthorizationV1Api()
    try:
        api.delete_cluster_role(name=name)
        logger.info(f"Deleted ClusterRole '{name}'")
    except kubernetes.client.exceptions.ApiException as e:
        if e.status == 404:
            logger.info(f"ClusterRole '{name}' already deleted")
        else:
            raise

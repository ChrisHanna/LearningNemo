"""Explicit operator-managed resource policy; expiry tags are not authority."""

MANAGED = 'operator-managed'


def managed(resource):
    tags = resource.get('tags') or {}
    return (tags.get('availabilityMode') == MANAGED and tags.get('owner') == 'learningnemo-portfolio'
        and tags.get('project') == 'learningnemo' and not tags.get('expiresAt'))


def dependency_mode(resources):
    modes = [managed(resource) for resource in resources]
    if any(modes) and not all(modes):
        raise ValueError('mixed availability policies; complete the coordinated transition')
    return MANAGED if all(modes) else 'leased'


def managed_tags(resource):
    tags = dict(resource.get('tags') or {})
    if tags.get('owner') != 'learningnemo-portfolio' or tags.get('project') != 'learningnemo':
        raise ValueError('availability transition requires owned resources')
    tags.pop('expiresAt', None)
    tags.update(availabilityMode=MANAGED, disposable='false')
    return tags
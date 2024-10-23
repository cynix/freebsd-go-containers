#!/usr/bin/env python3

import json
from ruamel.yaml import YAML


def main(projects) -> None:
    yaml = YAML()
    yaml.indent(sequence=4, offset=2)

    with open('dispatch.yaml') as y:
        dispatch = yaml.load(y)

    dispatch['on']['workflow_dispatch']['inputs'] = {x: {'type': 'string', 'required': False} for x in projects.keys()}
    dispatch['jobs']['dispatch']['strategy']['matrix']['include'] = [{
        'project': k,
        'repo': v['repo'],
        'packages': json.dumps([x for x in v['packages'].keys()]),
        'containers': json.dumps([k for k, v in v['packages'].items() if 'container' in v]),
    } for k, v in projects.items()]

    with open('.github/workflows/dispatch.yaml', 'w') as f:
        yaml.dump(dispatch, f)


if __name__ == "__main__":
    with open('projects.yaml') as y:
        main(YAML().load(y))

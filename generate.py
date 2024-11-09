#!/usr/bin/env python3

import os
import subprocess
import sys
from collections import defaultdict
from github import Github
from ruamel.yaml import YAML
from textwrap import dedent


def goreleaser(project: str, package: str, yaml, config) -> None:
    goreleaser = {
        'version': 2,
        'project_name': package,
        'dist': '../dist',
        'archives': [{
            'format': 'tar.gz',
            'name_template': '{{ .ProjectName }}-{{ .Version }}-{{ .Os }}_{{ .Arch }}{{ with .Arm }}v{{ . }}{{ end }}{{ with .Mips }}_{{ . }}{{ end }}{{ if not (eq .Amd64 "v1") }}{{ .Amd64 }}{{ end }}',
            'files': config.get('files', ['LICENSE']),
        }],
        'release': {
            'disable': True,
        },
    }

    if b := config.get('before', []):
        goreleaser['before'] = {'hooks': b}

    template = config.get('build', {})

    template['flags'] = template.get('flags', []) + ['-trimpath']
    template['ldflags'] = template.get('ldflags', []) + ['-buildid=', '-extldflags=-static', '-s', '-w']
    template['targets'] = [f"freebsd_{x}" for x in config.get('arch', ['amd64', 'arm64'])]

    if 'env' not in template:
        if config.get('cgo'):
            template['env'] = [
                'CGO_ENABLED=1',
                'CGO_CFLAGS=--target={{ if eq .Arch "amd64" }}x86_64{{ else }}aarch64{{ end }}-unknown-freebsd --sysroot=/freebsd/{{ .Arch }}',
                'CGO_LDFLAGS=--target={{ if eq .Arch "amd64" }}x86_64{{ else }}aarch64{{ end }}-unknown-freebsd --sysroot=/freebsd/{{ .Arch }} -fuse-ld=lld',
                'PKG_CONFIG_SYSROOT_DIR=/freebsd/{{ .Arch }}',
                'PKG_CONFIG_PATH=/freebsd/{{ .Arch }}/usr/local/libdata/pkgconfig',
            ]
        else:
            template['env'] = ['CGO_ENABLED=0']

    goreleaser['builds'] = []

    for binary in config.get('packages', {}).get(package, {}).get('binaries', [project]):
        build = template | {'id': binary, 'binary': binary}
        build['main'] = build.get('main', './cmd/{binary}').format(binary=binary)
        goreleaser['builds'].append(build)

    with open('.goreleaser.yaml', 'w') as f:
        yaml.dump(goreleaser, f)


def container(project: str, tag: str, package: str, config):
    if tag == 'latest':
        for r in Github().get_user('cynix').get_repo('freebsd-go-containers').get_releases():
            if r.tag_name.startswith(f"{project}-"):
                release = r
                break
        else:
            raise RuntimeError(f"no release for {project}")
    else:
        release = Github().get_user('cynix').get_repo('freebsd-go-containers').get_release(f"{project}-{tag}")

    container = {
        'base': 'freebsd:static',
        'binaries': [project],
    } | config['packages'][package]['container']

    binaries = container['binaries']
    arch = config.get('arch', ['amd64', 'arm64'])
    copy = defaultdict(list)

    for k, v in container.get('files', {}).items():
        copy[v] = [f"dist/${{TARGETARCH}}/{k}"]

    if os.path.exists(f"{project}/{package}"):
        copy['/'] = [f"{project}/{package}/"]

    with open('Containerfile', 'w') as cf:
        if user := container.get('user'):
            if '=' in user:
                user, uid = user.split('=')

                print(dedent(f"""\
                    FROM ghcr.io/cynix/freebsd:minimal AS builder
                    RUN pw groupadd -n {user} -g {uid}
                    RUN pw useradd -n {user} -u {uid} -g {user} -d /nonexistent -s /sbin/nologin
                    """), file=cf)

                copy['!/etc/'].extend(f"/etc/{x}" for x in ('group', 'master.passwd', 'passwd', 'pwd.db', 'spwd.db'))

        print(f"FROM ghcr.io/cynix/{container['base']}", file=cf)

        for a in arch:
            os.makedirs(f"dist/{a}")

            url = None

            for asset in release.get_assets():
                if asset.browser_download_url.endswith(f"-freebsd_{a}.tar.gz"):
                    url = asset.browser_download_url
                    break
            else:
                raise RuntimeError(f"no asset for {a}")

            subprocess.check_call(['curl', '-sSL', '-o', f"dist/{a}.tar.gz", url])
            subprocess.check_call(['tar', '-C', f"dist/{a}", '-zxf', f"dist/{a}.tar.gz"])

            for b in binaries:
                if not os.path.exists(f"dist/{a}/{b}"):
                    raise RuntimeError(f"binary {b} not found")

        print('ARG TARGETARCH', file=cf)
        print(f"COPY --chown=root:wheel --chmod=0755 {' '.join(f'dist/${{TARGETARCH}}/{x}' for x in binaries)} /usr/local/bin/", file=cf)

        for dst, files in copy.items():
            if not files:
                continue

            if dst.startswith('!'):
                cmd = 'COPY --from=builder'
                dst = dst[1:]
            else:
                cmd = 'COPY'

            print(f"{cmd} {' '.join(files)} {dst}", file=cf)

        if user:
            print(f"USER {user}:{user}", file=cf)

        for env, value in config.get('env', {}):
            print(f"ENV {env}={value}", file=cf)

        print(f"ENTRYPOINT [\"/usr/local/bin/{binaries[0]}\"]", file=cf)

    with open('build.sh', 'w') as sh:
        platform = ','.join([f"freebsd/{x}" for x in arch])
        print(f"buildah build --manifest=ghcr.io/cynix/{package}:{tag} --network=host --platform={platform} --pull=always .", file=sh)
        print(f"buildah manifest push --all ghcr.io/cynix/{package}:{tag} docker://ghcr.io/cynix/{package}:{tag}", file=sh)
        print(f"buildah manifest push --all ghcr.io/cynix/{package}:{tag} docker://ghcr.io/cynix/{package}:latest", file=sh)

    os.chmod('build.sh', 0o755)


if __name__ == "__main__":
    project = sys.argv[2]

    with open('projects.yaml') as y:
        yaml = YAML()
        yaml.width = 4096
        yaml.indent(sequence=4, offset=2)

        config = yaml.load(y)

        if sys.argv[1] == 'goreleaser':
            goreleaser(project, sys.argv[3], yaml, config[project])
        elif sys.argv[1] == 'container':
            container(project, sys.argv[3], sys.argv[4], config[project])
        else:
            raise RuntimeError(f"invalid command: {sys.argv[1]}")

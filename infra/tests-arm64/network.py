#!/usr/bin/env python3
"""Apply rules only to the CI bridge; never flush host/POS firewall rules."""
import ipaddress
import json
import subprocess

BRIDGE = 'br-kodify-test'
CHAIN = 'KODIFY_TESTS'
NETWORK = 'kodify-ci-tests'

def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)

def main():
    exists = subprocess.run(['docker', 'network', 'inspect', NETWORK], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not exists:
        run(['docker', 'network', 'create', '--driver', 'bridge', '--opt', 'com.docker.network.bridge.name=' + BRIDGE,
             '--opt', 'com.docker.network.bridge.enable_icc=false', NETWORK], stdout=subprocess.DEVNULL)
    info = json.loads(subprocess.check_output(['docker', 'network', 'inspect', NETWORK]))[0]
    assert info['Options'].get('com.docker.network.bridge.name') == BRIDGE
    assert not info.get('EnableIPv6')
    subprocess.run(['iptables', '-w', '-N', CHAIN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    run(['iptables', '-w', '-F', CHAIN])
    destinations = {'0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8', '169.254.0.0/16',
                    '172.16.0.0/12', '192.168.0.0/16', '224.0.0.0/4', '240.0.0.0/4'}
    for iface in json.loads(subprocess.check_output(['ip', '-j', 'address'])):
        for address in iface.get('addr_info', []):
            if address['family'] == 'inet':
                destinations.add(str(ipaddress.ip_address(address['local'])) + '/32')
    for destination in sorted(destinations):
        run(['iptables', '-w', '-A', CHAIN, '-d', destination, '-j', 'REJECT'])
    run(['iptables', '-w', '-A', CHAIN, '-j', 'RETURN'])
    for chain, rule in [('DOCKER-USER', ['-i', BRIDGE, '-j', CHAIN]), ('INPUT', ['-i', BRIDGE, '-j', 'REJECT'])]:
        if subprocess.run(['iptables', '-w', '-C', chain] + rule, stderr=subprocess.DEVNULL).returncode:
            run(['iptables', '-w', '-I', chain, '1'] + rule)
    print('CI-only network guard active')

if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Render rules for the dedicated runtime UID, without changing other traffic."""
import ipaddress
import json
import pwd
import subprocess

uid = pwd.getpwnam('kodify-ci').pw_uid
addresses = subprocess.check_output(['ip', '-j', 'address'], text=True)
v4 = {'0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
      '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16'}
v6 = {'::1/128', 'fc00::/7', 'fe80::/10'}
for interface in json.loads(addresses):
    for info in interface.get('addr_info', []):
        address = ipaddress.ip_address(info['local'])
        if address.is_loopback or address.is_link_local:
            continue
        (v4 if address.version == 4 else v6).add(str(address))
v4 = {str(n) for n in ipaddress.collapse_addresses(ipaddress.IPv4Network(x) for x in v4)}
v6 = {str(n) for n in ipaddress.collapse_addresses(ipaddress.IPv6Network(x) for x in v6)}
print('table inet kodify_ci {')
print('  chain protect_host {')
print('    type filter hook output priority 0; policy accept;')
# The system resolver is the only deliberate loopback exception.
print(f'    meta skuid {uid} ip daddr 127.0.0.53 udp dport 53 accept')
print(f'    meta skuid {uid} ip daddr 127.0.0.53 tcp dport 53 accept')
print(f'    meta skuid {uid} ip daddr {{ ' + ', '.join(sorted(v4)) + ' } reject')
print(f'    meta skuid {uid} ip6 daddr {{ ' + ', '.join(sorted(v6)) + ' } reject')
print('  }')
print('}')

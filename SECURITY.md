# Security policy

## Reporting a vulnerability

Please report security issues **privately** through GitHub:
[Report a vulnerability](https://github.com/git-cgallagher/ansible-collection-molecule/security/advisories/new)
(the **Security** tab, then **Report a vulnerability**). Don't open a public issue for a security problem.

You can expect an acknowledgement within a week. This is a one-person project, so fixes are best effort.

## Supported versions

Only the latest release gets fixes.

## Scope

This collection is test infrastructure. It boots throwaway Windows VMs and containers on a CI
runner or developer machine, with well-known lab passwords that are reachable only on the
host's loopback. Reports about anything that would expose those labs beyond the host, leak a
real credential, or run untrusted downloads without checksum pinning are in scope.

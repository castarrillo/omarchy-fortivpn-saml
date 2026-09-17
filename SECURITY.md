# Security

The root controller is a small, fixed-profile bridge to OpenFortiVPN. The installer requires an explicit administrator command, records one permitted local UID in a root-owned systemd unit, and installs root-owned code. It does not add sudoers rules, disable certificate validation, download code, or accept arbitrary execution requests from the widget.

The system service uses `NoNewPrivileges`, `ProtectHome`, `ProtectSystem`, and a private temporary directory. It still has network-administration privileges and access to the existing VPN configuration, because OpenFortiVPN needs them. These settings are defense in depth, not a sandbox or a security certification.

Only the selected local user and root may access the control socket. Other processes running as that user share the same permission. Status contains gateway, interface, address, resolver, and traffic metadata. Tokens are transient, but memory is not cryptographically protected against the account owner or root.

Report security issues through this repository's GitHub security advisories when available. Do not post credentials, session tokens, private gateway addresses, certificate fingerprints, or private log excerpts in public issues. For ordinary bug reports, replace these values with fictional examples.

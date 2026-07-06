# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | ✅ Active development |

## Reporting a Vulnerability

If you discover a security vulnerability in Aelvoxim, please report it by email to **macor [at] gealss [dot] com**.

Please do **not** open a public GitHub issue for security vulnerabilities.

### What to include

To help us triage quickly, please include:
- A clear description of the vulnerability
- Steps to reproduce, including environment details (OS, version, configuration)
- Any proof-of-concept code or screenshots (if available)
- Your assessment of potential impact

### What to expect

- **Acknowledgment:** We will acknowledge receipt within **48 hours**.
- **Triage:** We will confirm the vulnerability and assess severity within **5 business days**.
- **Fix timeline:** We will provide an estimated timeline for a fix and keep you updated on progress.
- **Disclosure:** We appreciate responsible disclosure. We request that you not publicly disclose the vulnerability until we have released a fix. We are happy to coordinate disclosure dates and publicly acknowledge your contribution (with your permission).

## Scope

### In scope

The following are **in scope** for security reports:

- Authentication bypass (API key, session, license)
- Remote code execution via LLM output or user input
- Data leakage of sensitive configuration (API keys, credentials)
- Privilege escalation between user roles
- Injection attacks (prompt injection, SQL injection, command injection)

### Out of scope

The following are **out of scope**:

- Vulnerabilities in third-party dependencies that have already been publicly disclosed (please report them upstream unless they affect our specific usage pattern)
- Denial of service through excessive resource usage
- Social engineering attacks
- Theoretical vulnerabilities without a working proof of concept

## Safe Harbor

We consider security research conducted in good faith and in accordance with this policy to be:

- Authorized in relation to applicable anti-hacking laws
- Exempt from restrictions in our Terms of Service that would otherwise interfere with such research

We will not pursue legal action against individuals who follow this policy in good faith.

## Recognition

We maintain a [Security Hall of Fame](#) *(link to your acknowledgments page, or remove this section)* to recognize researchers who help improve Aelvoxim's security.

Currently, we do **not** offer a monetary bug bounty program. However, we are happy to publicly acknowledge your contribution (with your permission).

## Preferred Languages

English or Chinese (中文).

---

*This policy was last updated on 2026-07-06.*

# Security Guidelines

## Mandatory Security Checks

Before ANY commit:
- [ ] No hardcoded secrets (API keys, passwords, tokens, private keys)
- [ ] All user inputs validated
- [ ] SQL injection prevention (parameterized queries only)
- [ ] Error messages don't leak sensitive data (wallet addresses, balances, keys)
- [ ] Rate limiting on all endpoints

## Secret Management

- NEVER hardcode secrets in source code
- ALWAYS use environment variables or a secret manager
- Validate that required secrets are present at startup
- Rotate any secrets that may have been exposed
- `.env` is gitignored; credentials live there

## Security Response Protocol

If security issue found:
1. STOP immediately
2. Assess severity (CRITICAL/HIGH/MEDIUM/LOW)
3. Fix CRITICAL issues before continuing
4. Rotate any exposed secrets
5. Review related code for similar issues

TEST-ONLY TLS FIXTURES for `pam update`'s real-transport tests (M15 Task 12).

Every key here is TEST-ONLY. It protects nothing and secures nothing: the tests start a TLS server
on 127.0.0.1 and reach it through an injected resolver, so that `pam/update/fetch.py` can be driven
over a real TLS connection without a byte reaching GitHub.

  ca.pem                          "pam TEST-ONLY trusted CA" - the test trust store (`cafile=`)
  server.pem                      leaf signed by it, SANs = E3's three hosts (spike 10B)
  server.TEST-ONLY.key            its key
  untrusted-server.pem            leaf with the same SANs, signed by a CA nobody trusts
  untrusted-server.TEST-ONLY.key  its key

  wrong-host-ca.pem               "pam TEST-ONLY wrong-host CA" - a SECOND trust store, for the host-name test
  wrong-host-server.pem           leaf signed by it, SAN = DNS:not-github.example.test ONLY (no E3 host)
  wrong-host-server.TEST-ONLY.key its key

The wrong-host pair stands in for "signed by the trusted CA, issued for another name": the trusted
CA's key was not kept, so a new CA (made the same way, key discarded) is the trust anchor for that
one test. The certificate verifies; only its name is wrong.

The three CA private keys were NOT kept: regenerating means generating new CAs too.

HOW THEY WERE MADE (2026-11-19, OpenSSL 3.5.4 from Git for Windows, EC P-256, 7300 days, so they
expire in 2046), with `MSYS_NO_PATHCONV=1` so Git Bash leaves `-subj` alone:

  leaf.cnf:
    basicConstraints=critical,CA:FALSE
    keyUsage=critical,digitalSignature
    extendedKeyUsage=serverAuth
    subjectAltName=DNS:api.github.com,DNS:github.com,DNS:release-assets.githubusercontent.com
    subjectKeyIdentifier=hash
    authorityKeyIdentifier=keyid

  for who in trusted untrusted:
    openssl req -x509 -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
      -keyout $who-ca.key -out $who-ca.pem -days 7300 -subj "/CN=pam TEST-ONLY $who CA" \
      -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
      -addext "subjectKeyIdentifier=hash"
    openssl req -new -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
      -keyout $who-leaf.key -out $who-leaf.csr -subj "/CN=pam TEST-ONLY server ($who)"
    openssl x509 -req -in $who-leaf.csr -CA $who-ca.pem -CAkey $who-ca.key \
      -set_serial 0x$(openssl rand -hex 8) -out $who-leaf.pem -days 7300 -extfile leaf.cnf

  then: trusted-ca.pem -> ca.pem, trusted-leaf.pem -> server.pem, untrusted-leaf.pem ->
  untrusted-server.pem, and each leaf key copied with the two-line TEST-ONLY label in front of
  its PEM block (OpenSSL skips text before `-----BEGIN`).

`tests/test_update_fetch.py::test_the_fixture_certificates_last_at_least_ten_years` pins every
certificate's notAfter at or after 2036-11-19, ten years from the day they were made.

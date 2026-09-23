# syntax=docker/dockerfile:1.7

ARG BUILD_BASE_IMAGE
ARG NATIVE_BUILD_BASE_IMAGE
ARG RUNTIME_BASE_IMAGE
FROM ${BUILD_BASE_IMAGE} AS build

WORKDIR /build
COPY containers/trusted-runtime.requirements.lock ./requirements.lock
RUN python -m pip install \
    --disable-pip-version-check \
    --no-cache-dir \
    --require-hashes \
    --target /opt/learningnemo \
    -r requirements.lock
RUN mkdir -p /opt/runtime-metadata \
    && dpkg-query --status libffi8 > /opt/runtime-metadata/libffi8.status \
    && dpkg-query --status libssl3t64 > /opt/runtime-metadata/libssl3t64.status \
    && dpkg-query --status libzstd1 > /opt/runtime-metadata/libzstd1.status

COPY scripts/extract-runtime-library.py /tmp/extract-runtime-library.py
RUN python /tmp/extract-runtime-library.py
COPY scripts/extract-zlib-ng-source.py /tmp/extract-zlib-ng-source.py
RUN python /tmp/extract-zlib-ng-source.py

COPY src/task_agent/__init__.py /opt/learningnemo/task_agent/__init__.py
COPY src/task_agent/control /opt/learningnemo/task_agent/control
COPY scripts/apply-sql-migrations.py /opt/learningnemo/scripts/apply-sql-migrations.py
COPY scripts/probe-sql-connectivity.py /opt/learningnemo/scripts/probe-sql-connectivity.py
COPY scripts/probe-sql-odbc.py /opt/learningnemo/scripts/probe-sql-odbc.py
COPY scripts/run-live-cycle-workflow.py /opt/learningnemo/scripts/run-live-cycle-workflow.py
COPY scripts/verify-runtime-native.py /opt/learningnemo/scripts/verify-runtime-native.py

FROM ${NATIVE_BUILD_BASE_IMAGE} AS native-build

SHELL ["/usr/bin/bash", "-euo", "pipefail", "-c"]
WORKDIR /build/zlib-ng
COPY --from=build /opt/zlib-ng-source /build/zlib-ng
RUN ./configure --zlib-compat --without-optimizations --prefix=/opt/zlib-ng \
    && make -j2 \
    && make test \
    && make install

FROM ${RUNTIME_BASE_IMAGE}

ENV PATH=/usr/local/bin:/usr/local/sbin:/usr/bin:/bin \
    LD_LIBRARY_PATH=/usr/local/lib:/usr/lib \
    OPENSSL_CONF=/etc/pki/tls/openssl.cnf \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/learningnemo \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/pki/tls/cert.pem
WORKDIR /opt/learningnemo
COPY --from=build /usr/local /usr/local
COPY --from=build /opt/learningnemo /opt/learningnemo
COPY --from=build /opt/runtime-libs/usr/lib/libltdl.so.7.3.2 /usr/lib/libltdl.so.7
COPY --from=build /usr/lib/x86_64-linux-gnu/libcrypto.so.3 /usr/local/lib/libcrypto.so.3
COPY --from=build /usr/lib/x86_64-linux-gnu/libssl.so.3 /usr/local/lib/libssl.so.3
COPY --from=build /usr/lib/x86_64-linux-gnu/libzstd.so.1.5.7 /usr/local/lib/libzstd.so.1
COPY --from=build /usr/lib/x86_64-linux-gnu/libffi.so.8.1.4 /usr/lib/libffi.so.8.1.4
COPY --from=build /usr/share/doc/libffi8/copyright /usr/share/licenses/libffi8/copyright
COPY --from=build /usr/share/doc/libssl3t64/copyright /usr/share/licenses/libssl3t64/copyright
COPY --from=build /usr/share/doc/libzstd1/copyright /usr/share/licenses/libzstd1/copyright
COPY --from=build /opt/runtime-metadata/libffi8.status /var/lib/dpkg/status.d/libffi8
COPY --from=build /opt/runtime-metadata/libssl3t64.status /var/lib/dpkg/status.d/libssl3t64
COPY --from=build /opt/runtime-metadata/libzstd1.status /var/lib/dpkg/status.d/libzstd1
COPY --from=native-build /opt/zlib-ng/lib/libz.so.1.3.1.zlib-ng /usr/lib/libz.so.1.3.1.zlib-ng
COPY --from=native-build /build/zlib-ng/LICENSE.md /usr/share/licenses/zlib-ng/LICENSE.md
COPY --from=native-build /usr/lib/libgcc_s.so.1 /usr/lib/libgcc_s.so.1
COPY --from=native-build /usr/lib/libstdc++.so.6.0.36 /usr/lib/libstdc++.so.6.0.36
COPY --from=native-build /usr/lib/libcom_err.so.2.1 /usr/lib/libcom_err.so.2.1
COPY --from=native-build /usr/lib/libgssapi_krb5.so.2.2 /usr/lib/libgssapi_krb5.so.2.2
COPY --from=native-build /usr/lib/libk5crypto.so.3.1 /usr/lib/libk5crypto.so.3.1
COPY --from=native-build /usr/lib/libkeyutils.so.1.10 /usr/lib/libkeyutils.so.1.10
COPY --from=native-build /usr/lib/libkrb5.so.3.3 /usr/lib/libkrb5.so.3.3
COPY --from=native-build /usr/lib/libkrb5support.so.0.1 /usr/lib/libkrb5support.so.0.1
COPY --from=native-build /var/lib/db/sbom/keyutils-libs-1.6.3-r40.spdx.json /var/lib/db/sbom/keyutils-libs-1.6.3-r40.spdx.json
COPY --from=native-build /var/lib/db/sbom/krb5-libs-1.22.2-r3.spdx.json /var/lib/db/sbom/krb5-libs-1.22.2-r3.spdx.json
COPY --from=native-build /var/lib/db/sbom/libgcc-16.2.0-r1.spdx.json /var/lib/db/sbom/libgcc-16.2.0-r1.spdx.json
COPY --from=native-build /var/lib/db/sbom/libstdc++-16.2.0-r1.spdx.json /var/lib/db/sbom/libstdc++-16.2.0-r1.spdx.json
COPY containers/zlib-ng-2.3.3.spdx.json /var/lib/db/sbom/zlib-ng-2.3.3.spdx.json
COPY scripts/prepare-trusted-runtime.py /tmp/prepare-trusted-runtime.py

USER 0:0
RUN ["python3", "/tmp/prepare-trusted-runtime.py"]

RUN ["python3", "/opt/learningnemo/scripts/verify-runtime-native.py"]

USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/python3", "-m", "task_agent.control.runtime"]
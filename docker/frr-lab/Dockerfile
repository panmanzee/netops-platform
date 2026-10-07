# frr-lab node image: FRRouting + sshd + an `automation` user whose login shell
# is vtysh, so a Containerlab node behaves like an SSH-managed router.
# Build with docker/frr-lab/build.sh (do not `docker build` this dir directly —
# the build context needs a pubkey that is not committed). Default tag frr-lab:local.
ARG FRR_VERSION=10.7.1
FROM quay.io/frrouting/frr:${FRR_VERSION}

# frr images are Alpine-based
RUN apk add --no-cache openssh-server openssh-keygen \
    && ssh-keygen -A

# automation user: vtysh is the login shell, key auth only.
# adduser -D leaves the account password-locked ("!" in /etc/shadow); with
# UsePAM off (Alpine default) sshd treats a locked account as an invalid user
# and refuses even pubkey auth, so swap "!" -> "*" (no usable password, but the
# account is not locked).
RUN adduser -D -s /usr/bin/vtysh automation \
    && addgroup automation frrvty \
    && addgroup automation frr \
    && sed -i 's/^automation:!:/automation:*:/' /etc/shadow \
    && mkdir -p /home/automation/.ssh \
    && chmod 700 /home/automation/.ssh
COPY id_ed25519.pub /home/automation/.ssh/authorized_keys
RUN chown -R automation:automation /home/automation/.ssh \
    && chmod 600 /home/automation/.ssh/authorized_keys

# sshd: key auth only, no passwords, allow the automation user
RUN sed -i \
    -e 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' \
    -e 's/^#\?PermitRootLogin.*/PermitRootLogin no/' \
    -e 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' \
    /etc/ssh/sshd_config \
    && echo 'AllowUsers automation' >> /etc/ssh/sshd_config

COPY daemons /etc/frr/daemons
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# seed empty config files so a config-less node still gives a clean vtysh
# (labs bind-mount their own /etc/frr/frr.conf on top of this)
RUN touch /etc/frr/frr.conf /etc/frr/vtysh.conf \
    && echo 'service integrated-vtysh-config' > /etc/frr/vtysh.conf \
    && chown frr:frr /etc/frr/frr.conf /etc/frr/vtysh.conf \
    && chmod 640 /etc/frr/frr.conf /etc/frr/vtysh.conf

EXPOSE 22
ENTRYPOINT ["/entrypoint.sh"]

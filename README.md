# docker-proxy-client

Works best with [docker-proxy-server](https://github.com/0xf00f00/docker-proxy-server). Use the same tags/releases to get compatible versions of server and client.

## How to setup

### Prerequisites

1. Install Docker

```bash
apt-get update
apt-get install -y git curl ca-certificates
curl -fsSL https://get.docker.com | sh
```

2. Install Docker Compose

```bash
sudo apt-get install -y docker-compose
```

1. Rename the `.env.example` file to `.env` (or copy: `cp .env.example .env`), and fill the values in the `.env` file.
2. Run the containers using `docker-compose`
3. Copy `./system/vpn-route.sh` to `/usr/local/bin/vpn-route.sh`
4. Copy `./system/vpn-route.service` to `/etc/systemd/system/vpn-route.service`
5. Fill the variables in `/etc/systemd/system/vpn-route.service` as needed
6. Copy `./system/vpn-route.timer` to `/etc/systemd/system/vpn-route.timer`
7. `sudo systemctl enable vpn-route.service && sudo systemctl start vpn-route.service`
8. `sudo systemctl enable vpn-route.timer && sudo systemctl start vpn-route.timer`
9. Create `/etc/network/interfaces.d/utun` and add:
```
auto utun
iface utun inet manual
    up route add default dev utun
```

```bash
docker-compose up -d
```

# docker-proxy-client

Works best with [docker-proxy-server](https://github.com/0xf00f00/docker-proxy-server). Use the same tags/releases to get compatible versions of server and client.

## How to setup

1. Rename the `.env.example` file to `.env` (or copy: `cp .env.example .env`), and fill the values in the `.env` file.
2. Run the containers using `docker-compose`
3. Copy `./system/check_and_set_utun_route.sh` to `/usr/local/bin/check_and_set_utun_route.sh`
4. Copy `./system/utun-route.service` to `/etc/systemd/system/utun-route.service`
5. Copy `./system/utun-route.timer` to `/etc/systemd/system/utun-route.timer`
6. `sudo systemctl enable utun-route.service && sudo systemctl start utun-route.service`
7. `sudo systemctl enable utun-route.timer && sudo systemctl start utun-route.timer`
8. Create `/etc/network/interfaces.d/utun` and add:
```
auto utun
iface utun inet manual
    up route add default dev utun
```

```bash
docker-compose up -d
```

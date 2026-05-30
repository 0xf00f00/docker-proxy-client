const SOCKS_CAPABLE = new Set(["socks5", "mixed"]);

export function isSocksCapable(protocol: string): boolean {
  return SOCKS_CAPABLE.has(protocol.split("+")[0] ?? "");
}

export function telegramSocksLink(
  host: string,
  port: string,
  user?: string,
  pass?: string,
): string {
  const params = new URLSearchParams({ server: host, port });
  if (user) params.set("user", user);
  if (pass) params.set("pass", pass);
  return `https://t.me/socks?${params.toString()}`;
}

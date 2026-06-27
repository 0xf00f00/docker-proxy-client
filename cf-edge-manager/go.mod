module github.com/0xf00f00/cf-edge-manager

go 1.25.6

require (
	github.com/refraction-networking/utls v1.8.2
	github.com/robfig/cron/v3 v3.0.1
	golang.org/x/net v0.55.0
	sni-spoofing-go v0.0.0
)

require (
	github.com/andybalholm/brotli v1.0.6 // indirect
	github.com/florianl/go-nfqueue/v2 v2.0.3 // indirect
	github.com/google/go-cmp v0.6.0 // indirect
	github.com/josharian/native v1.1.0 // indirect
	github.com/klauspost/compress v1.17.4 // indirect
	github.com/mdlayher/netlink v1.7.2 // indirect
	github.com/mdlayher/socket v0.4.1 // indirect
	github.com/one-api/godivert v0.0.0-20260524182449-caf178e4c0fb // indirect
	golang.org/x/crypto v0.51.0 // indirect
	golang.org/x/sync v0.7.0 // indirect
	golang.org/x/sys v0.45.0 // indirect
)

replace sni-spoofing-go => github.com/aleskxyz/SNI-Spoofing-Go v0.7.2

// sni-spoofing-go's own replace doesn't transit to us; mirror it.
replace github.com/one-api/godivert => github.com/aleskxyz/godivert v0.0.0-20260524182449-caf178e4c0fb

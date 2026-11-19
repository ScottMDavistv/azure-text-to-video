// Chromium WebRTC IP-handling policy: non-proxied UDP is forbidden.
// Playwright's route and routeWebSocket do not intercept STUN packets.
// Do not replace this with JavaScript-level RTCPeerConnection monkeypatching.
export const WEBRTC_UDP_POLICY_ARG = '--force-webrtc-ip-handling-policy=disable_non_proxied_udp'

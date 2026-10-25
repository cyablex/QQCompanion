Source: https://github.com/6shenhonghong9/dsh-startup-screen
Version: 1.1.1
Commit: 8ec3132e69f0906b3ed2f7eb083b1035cd69c1d4
Original splash.css is unmodified. Local adaptations in splash.js reuse its rotating reticle for real loading and QR sign-in, await the optional __DAFEIYU_WAIT_READY__ hook before Welcome, and let skip fast-forward to that loading scene. companion.css positions sign-in content and controls within the original scene. The host sends read-only state/images and receives explicit button events; the sandboxed iframe has no native bridge. MIT license retained. Third-party audio assets are not bundled; voice and sound are disabled.

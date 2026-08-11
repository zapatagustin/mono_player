{
  description = "Native Wayland YouTube client -- Qt Quick UI over libmpv";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };

      python = pkgs.python313.withPackages (ps: with ps; [
        pyside6 # Qt Quick UI
        yt-dlp # binary, consumed by mpv's ytdl_hook (this env's bin wins PATH)
        # PO token plugin (auto-discovered by yt-dlp via sys.path) + the Node
        # server it talks to, shipped in share/; main.py manages the server.
        bgutil-ytdlp-pot-provider
        gpsoauth # Google master-token auth -- the flow microG implements
        keyring # master token goes to the Secret Service, never to a file
        httpx # one pooled HTTP/2 client for thumbnails and InnerTube
        h2 # httpx's http2 extra; without it http2=True raises at runtime
        # asyncio event loop riding Qt's -- httpx needs a live loop. Its GC
        # stale-reference tests are timing-flaky in the sandbox, and its
        # import check needs a Qt impl the build env lacks; pyside6 provides
        # it in this env at runtime.
        (qasync.overridePythonAttrs (_: { doCheck = false; pythonImportsCheck = [ ]; }))
      ]);
    in
    {
      devShells.${system}.default = pkgs.mkShell {
        packages = [
          python
          pkgs.mpv # CLI, to A/B decode behaviour against the app
          pkgs.mpv-unwrapped.dev # libmpv headers for the C++ QQuickItem bridge
          pkgs.qt6.qtdeclarative # Qt Quick, for compiling that bridge
          pkgs.cmake
          pkgs.pkg-config
          pkgs.libva-utils # vainfo
          pkgs.nodejs # bgutil PO token provider
        ];

        # Wayland has no foreign-window embedding, so the video is rendered into a
        # QQuickFramebufferObject via mpv_render_context rather than handed a window
        # id. Nothing here enforces that -- it is why qtdeclarative and the libmpv
        # headers are both in this shell.

        # Point libva at this flake's driver so the shell hardware-decodes before
        # the system-level change (nixos-config, hardware.graphics.extraPackages)
        # has been activated. Redundant but harmless once it has: same driver.
        LIBVA_DRIVERS_PATH = "${pkgs.intel-media-driver}/lib/dri";

        # PySide6's engine does not see nixpkgs' QML modules (QtQuick.Controls
        # et al. live in qtdeclarative's own store path; QtWebEngine -- used
        # by the login screen only -- in qtwebengine's).
        QML_IMPORT_PATH = "${pkgs.qt6.qtdeclarative}/lib/qt-6/qml:${pkgs.qt6.qtwebengine}/lib/qt-6/qml";

        # YouTube serves thumbnails as WebP; Qt needs the qtimageformats
        # plugins to decode them (base Qt only does png/jpg/gif).
        QT_PLUGIN_PATH = "${pkgs.qt6.qtimageformats}/lib/qt-6/plugins";
      };
    };
}

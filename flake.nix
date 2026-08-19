{
  description = "Native Wayland YouTube client -- Qt Quick UI over libmpv";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = import nixpkgs { inherit system; };

      # yt-dlp pinned to nightly: YouTube 403-enforces stale player clients
      # (SABR/PO-token rollout); stable 2026.07.04 lacks the visionos client
      # and a month of client-version updates. Interpreter-level override so
      # bgutil-ytdlp-pot-provider's propagated dependency is the same drv.
      # Re-point rev+hash at the current nightly (yt-dlp-nightly-builds
      # release body names the commit) whenever loads start 403ing again;
      # drop the override once a stable release catches up in nixpkgs.
      python313 = pkgs.python313.override {
        packageOverrides = _: prev: {
          yt-dlp = prev.yt-dlp.overridePythonAttrs (_: {
            # master's version.py still carries the last stable string and
            # the metadata check requires a match; the src rev is what counts.
            version = "2026.7.4";
            src = pkgs.fetchFromGitHub {
              owner = "yt-dlp";
              repo = "yt-dlp";
              # pinned-nightly-date: 2026-08-04 (update with rev; check.sh warns past 30d)
              rev = "5d6b8c8cd19785c3086ae3a9ec618c45e25eb3bc";
              hash = "sha256:17wf454dpplgqsxmn58hsg7vkw627lws4q0k2pv2af50b434scml";
            };
          });
        };
      };

      python = python313.withPackages (ps: with ps; [
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

      # Shared between the dev shell and the package below, so there is one
      # place to update when a Qt module moves. PySide6's engine does not see
      # nixpkgs' QML modules (QtQuick.Controls et al. live in qtdeclarative's
      # own store path; QtWebEngine -- used by the login screen only -- in
      # qtwebengine's).
      qmlImportPath = "${pkgs.qt6.qtdeclarative}/lib/qt-6/qml:${pkgs.qt6.qtwebengine}/lib/qt-6/qml";

      # YouTube serves thumbnails as WebP; Qt needs the qtimageformats
      # plugins to decode them (base Qt only does png/jpg/gif).
      qtPluginPath = "${pkgs.qt6.qtimageformats}/lib/qt-6/plugins";

      # The C++ QQuickItem bridge (bridge/CMakeLists.txt) builds a QML plugin
      # only -- qt_add_qml_module emits no install() rule of its own, so the
      # package derivation below copies its build output directory by hand
      # rather than relying on `cmake --install`.
      mono_player = pkgs.stdenv.mkDerivation {
        pname = "mono_player";
        # No version scheme in this repo yet (no tags, no pyproject.toml);
        # the flake's own rev is the least made-up thing to call this.
        version = self.shortRev or self.dirtyShortRev or "dev";
        src = self;

        nativeBuildInputs = [ pkgs.cmake pkgs.pkg-config pkgs.makeWrapper pkgs.patchelf ];
        # qtdeclarative for Qt6::Quick, mpv-unwrapped.dev for the pkg-config
        # mpv module the bridge links against.
        buildInputs = [ pkgs.qt6.qtdeclarative pkgs.mpv-unwrapped.dev ];
        # qtdeclarative's setup hook otherwise demands wrapQtAppsHook; this
        # derivation ships a plain-makeWrapper wrapper instead (see
        # installPhase), so opt out of Qt's own app-wrapping machinery.
        dontWrapQtApps = true;

        configurePhase = ''
          cmake -S bridge -B build -DCMAKE_BUILD_TYPE=Release
        '';

        buildPhase = ''
          # qt_add_qml_module's PLUGIN_TARGET makes "mpvbridgeplugin" (the
          # loadable plugin qmldir points at) a separate target from the
          # "mpvbridge" backing library it links against -- both are needed.
          cmake --build build -j"$NIX_BUILD_CORES" --target mpvbridgeplugin
        '';

        # main.py resolves every path (app/, qml/, and the bridge's QML
        # import path) relative to its own location, so the install layout
        # mirrors the repo layout exactly -- no path patching needed, and
        # the same main.py still runs unmodified from the dev shell.
        installPhase = ''
          runHook preInstall

          dest=$out/share/mono_player
          mkdir -p "$dest"/bridge/build
          cp main.py "$dest"/
          cp -r app qml "$dest"/
          # libmpvbridgeplugin.so (in MpvBridge/) links against libmpvbridge.so
          # (one level up, in build/) -- the same relative layout main.py's
          # addImportPath expects is preserved here so a $ORIGIN-relative
          # rpath below can replace the build sandbox's absolute one.
          cp -r build/MpvBridge "$dest"/bridge/build/
          cp build/libmpvbridge.so "$dest"/bridge/build/
          plugin="$dest"/bridge/build/MpvBridge/libmpvbridgeplugin.so
          old_rpath=$(patchelf --print-rpath "$plugin")
          patchelf --set-rpath "\$ORIGIN/..:''${old_rpath#*:}" "$plugin"

          mkdir -p $out/bin
          # Plain makeWrapper, not qt6's wrapQtAppsHook: that hook wraps C++
          # Qt binaries it finds under $out/bin, and would double-wrap this
          # one on top of the env vars set here for a PySide6 app that has
          # no Qt binary of its own to begin with.
          makeWrapper ${python}/bin/python3 $out/bin/mono_player \
            --add-flags "$dest/main.py" \
            --prefix PATH : "${python}/bin:${pkgs.nodejs}/bin" \
            --set QML_IMPORT_PATH "${qmlImportPath}" \
            --set QT_PLUGIN_PATH "${qtPluginPath}"

          mkdir -p $out/share/applications
          cat > $out/share/applications/mono_player.desktop <<EOF
          [Desktop Entry]
          Type=Application
          Name=mono_player
          Exec=mono_player
          Terminal=false
          Categories=AudioVideo;Video;Player;
          EOF

          runHook postInstall
        '';

        meta = {
          description = "Native Wayland YouTube client -- Qt Quick UI over libmpv";
          mainProgram = "mono_player";
          platforms = [ "x86_64-linux" ];
        };
      };
    in
    {
      packages.${system}.default = mono_player;

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

        QML_IMPORT_PATH = qmlImportPath;
        QT_PLUGIN_PATH = qtPluginPath;
      };
    };
}

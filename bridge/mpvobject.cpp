// Adapted from mpv-examples/libmpv/qml (render API + QQuickFramebufferObject),
// the reference Qt Quick integration for Wayland where window embedding does
// not exist.
#include "mpvobject.h"
#include "node_conv.h"

#include <QGuiApplication>
#include <QJSValue>
#include <QOpenGLContext>
#include <QtGui/qguiapplication_platform.h>
#include <QOpenGLFramebufferObject>
#include <QtQuick/QQuickOpenGLUtils>
#include <QtQuick/QQuickWindow>
#include <clocale>
#include <cstring>

static void *getProcAddressMpv(void *, const char *name)
{
    QOpenGLContext *glctx = QOpenGLContext::currentContext();
    if (!glctx)
        return nullptr;
    return reinterpret_cast<void *>(glctx->getProcAddress(QByteArray(name)));
}

class MpvRenderer : public QQuickFramebufferObject::Renderer
{
public:
    explicit MpvRenderer(const MpvObject *obj) : obj_(obj) {}

    // Called on the render thread with the GL context current -- the only
    // place the mpv_render_context can be created.
    QOpenGLFramebufferObject *createFramebufferObject(const QSize &size) override
    {
        if (!obj_->mpv_gl) {
            mpv_opengl_init_params gl_init_params{getProcAddressMpv, nullptr};
            // Hand mpv the compositor connection: without it the vaapi
            // interop cannot create a VA display and hwdec degrades to
            // vaapi-copy (a per-frame GPU->RAM->GL round trip).
            void *wl_display = nullptr;
            if (auto *wayland =
                    qGuiApp->nativeInterface<QNativeInterface::QWaylandApplication>())
                wl_display = wayland->display();
            mpv_render_param params[]{
                {MPV_RENDER_PARAM_API_TYPE,
                 const_cast<char *>(MPV_RENDER_API_TYPE_OPENGL)},
                {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &gl_init_params},
                {wl_display ? MPV_RENDER_PARAM_WL_DISPLAY
                            : MPV_RENDER_PARAM_INVALID, wl_display},
                {MPV_RENDER_PARAM_INVALID, nullptr}};
            if (mpv_render_context_create(&obj_->mpv_gl, obj_->mpv, params) < 0)
                qFatal("mpv: failed to initialize render context");
            mpv_render_context_set_update_callback(
                obj_->mpv_gl, MpvObject::onMpvRedraw,
                const_cast<MpvObject *>(obj_));
        }
        return QQuickFramebufferObject::Renderer::createFramebufferObject(size);
    }

    void render() override
    {
        QOpenGLFramebufferObject *fbo = framebufferObject();
        mpv_opengl_fbo mpfbo{static_cast<int>(fbo->handle()), fbo->width(),
                             fbo->height(), 0};
        int flip_y{0};
        mpv_render_param params[]{
            {MPV_RENDER_PARAM_OPENGL_FBO, &mpfbo},
            {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
            {MPV_RENDER_PARAM_INVALID, nullptr}};
        mpv_render_context_render(obj_->mpv_gl, params);
        QQuickOpenGLUtils::resetOpenGLState();
    }

private:
    const MpvObject *obj_;
};

MpvObject::MpvObject(QQuickItem *parent) : QQuickFramebufferObject(parent)
{
    // libmpv refuses to run under a non-C numeric locale, and
    // Q(Gui)Application sets the system locale on startup.
    std::setlocale(LC_NUMERIC, "C");

    mpv = mpv_create();
    if (!mpv)
        qFatal("mpv: mpv_create failed");

    // Render through our render context instead of a core-owned window --
    // without this mpv opens its own Wayland window (render.h requires it).
    mpv_set_option_string(mpv, "vo", "libmpv");
    // libmpv disables ytdl by default, unlike the mpv CLI.
    mpv_set_option_string(mpv, "ytdl", "yes");
    // MONO_HWDEC overrides for diagnosis/calibration (e.g. "vaapi-copy"
    // trades ~2.3x CPU for isolating zero-copy interop issues).
    const QByteArray hwdec = qgetenv("MONO_HWDEC");
    mpv_set_option_string(mpv, "hwdec",
                          hwdec.isEmpty() ? "auto-safe" : hwdec.constData());
    mpv_request_log_messages(mpv, "info");

    if (mpv_initialize(mpv) < 0)
        qFatal("mpv: mpv_initialize failed");

    mpv_observe_property(mpv, 0, "playlist-pos", MPV_FORMAT_INT64);
    mpv_observe_property(mpv, 0, "playback-time", MPV_FORMAT_DOUBLE);
    mpv_observe_property(mpv, 0, "duration", MPV_FORMAT_DOUBLE);
    mpv_observe_property(mpv, 0, "pause", MPV_FORMAT_FLAG);
    mpv_set_wakeup_callback(mpv, MpvObject::wakeup, this);
    // Orientation: mpv draws into the FBO with flip_y=0 and the scene graph
    // samples it upright as-is — no mirrorVertically, no flip. Adding either
    // shows the video upside down (verified on Wayland/EGL).
}

MpvObject::~MpvObject()
{
    // Per the render API contract, the render context must be freed before
    // the handle. At this point the window is gone and rendering has stopped.
    if (mpv_gl)
        mpv_render_context_free(mpv_gl);
    mpv_terminate_destroy(mpv);
}

QQuickFramebufferObject::Renderer *MpvObject::createRenderer() const
{
    return new MpvRenderer(this);
}

// QML hands JS arrays/objects to QVariant parameters as wrapped QJSValues;
// unwrap them so node_conv sees plain QVariantList/QVariantMap.
static QVariant unwrapJs(const QVariant &v)
{
    if (v.typeId() == qMetaTypeId<QJSValue>())
        return v.value<QJSValue>().toVariant();
    return v;
}

void MpvObject::command(const QVariant &params)
{
    NodeBuilder b(unwrapJs(params));
    int err = mpv_command_node(mpv, b.node(), nullptr);
    if (err < 0)
        emit logMessage(QStringLiteral("bridge"), QStringLiteral("error"),
                        QStringLiteral("command (%1): %2")
                            .arg(QString::fromUtf8(params.typeName()),
                                 QString::fromUtf8(mpv_error_string(err))));
}

void MpvObject::setProperty(const QString &name, const QVariant &value)
{
    NodeBuilder b(unwrapJs(value));
    int err = mpv_set_property(mpv, name.toUtf8().constData(),
                               MPV_FORMAT_NODE, b.node());
    if (err < 0)
        emit logMessage(QStringLiteral("bridge"), QStringLiteral("error"),
                        name + QStringLiteral(": ") +
                            QString::fromUtf8(mpv_error_string(err)));
}

QVariant MpvObject::getProperty(const QString &name) const
{
    mpv_node result;
    if (mpv_get_property(mpv, name.toUtf8().constData(), MPV_FORMAT_NODE,
                         &result) < 0)
        return QVariant();
    QVariant v = nodeToVariant(&result);
    mpv_free_node_contents(&result);
    return v;
}

void MpvObject::observe(const QString &name)
{
    mpv_observe_property(mpv, 1, name.toUtf8().constData(), MPV_FORMAT_NODE);
}

void MpvObject::onMpvRedraw(void *ctx)
{
    QMetaObject::invokeMethod(static_cast<MpvObject *>(ctx), "doUpdate",
                              Qt::QueuedConnection);
}

void MpvObject::doUpdate()
{
    update();
}

void MpvObject::wakeup(void *ctx)
{
    QMetaObject::invokeMethod(static_cast<MpvObject *>(ctx), "onMpvEvents",
                              Qt::QueuedConnection);
}

void MpvObject::onMpvEvents()
{
    while (mpv) {
        mpv_event *event = mpv_wait_event(mpv, 0);
        if (event->event_id == MPV_EVENT_NONE)
            break;
        handleEvent(event);
    }
}

void MpvObject::handleEvent(mpv_event *event)
{
    switch (event->event_id) {
    case MPV_EVENT_PROPERTY_CHANGE: {
        auto *prop = static_cast<mpv_event_property *>(event->data);
        if (event->reply_userdata == 1) {
            // Dynamically observed (observe()): NODE payload, generic signal.
            // FORMAT_NONE means the property became unavailable.
            emit propertyChanged(
                QString::fromUtf8(prop->name),
                prop->format == MPV_FORMAT_NODE
                    ? nodeToVariant(static_cast<mpv_node *>(prop->data))
                    : QVariant());
            break;
        }
        if (std::strcmp(prop->name, "playlist-pos") == 0 &&
            prop->format == MPV_FORMAT_INT64)
            emit playlistPosChanged(*static_cast<qint64 *>(prop->data));
        else if (std::strcmp(prop->name, "playback-time") == 0 &&
                 prop->format == MPV_FORMAT_DOUBLE)
            emit playbackTimeChanged(*static_cast<double *>(prop->data));
        else if (std::strcmp(prop->name, "duration") == 0 &&
                 prop->format == MPV_FORMAT_DOUBLE)
            emit durationChanged(*static_cast<double *>(prop->data));
        else if (std::strcmp(prop->name, "pause") == 0 &&
                 prop->format == MPV_FORMAT_FLAG)
            emit pauseChanged(*static_cast<int *>(prop->data) != 0);
        break;
    }
    case MPV_EVENT_END_FILE: {
        auto *ef = static_cast<mpv_event_end_file *>(event->data);
        emit endFile(ef->reason == MPV_END_FILE_REASON_ERROR);
        break;
    }
    case MPV_EVENT_LOG_MESSAGE: {
        auto *msg = static_cast<mpv_event_log_message *>(event->data);
        emit logMessage(QString::fromUtf8(msg->prefix),
                        QString::fromUtf8(msg->level),
                        QString::fromUtf8(msg->text).trimmed());
        break;
    }
    default:
        break;
    }
}

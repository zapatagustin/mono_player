#include "node_conv.h"

#include <QVariantList>
#include <QVariantMap>
#include <cstdlib>
#include <cstring>

QVariant nodeToVariant(const mpv_node *node)
{
    switch (node->format) {
    case MPV_FORMAT_FLAG:
        return QVariant(node->u.flag != 0);
    case MPV_FORMAT_INT64:
        return QVariant(qint64(node->u.int64));
    case MPV_FORMAT_DOUBLE:
        return QVariant(node->u.double_);
    case MPV_FORMAT_STRING:
        return QVariant(QString::fromUtf8(node->u.string));
    case MPV_FORMAT_NODE_ARRAY: {
        QVariantList list;
        list.reserve(node->u.list->num);
        for (int i = 0; i < node->u.list->num; i++)
            list.append(nodeToVariant(&node->u.list->values[i]));
        return QVariant(list);
    }
    case MPV_FORMAT_NODE_MAP: {
        QVariantMap map;
        for (int i = 0; i < node->u.list->num; i++)
            map.insert(QString::fromUtf8(node->u.list->keys[i]),
                       nodeToVariant(&node->u.list->values[i]));
        return QVariant(map);
    }
    default: // NONE and formats we do not handle degrade to invalid
        return QVariant();
    }
}

static void buildNode(mpv_node *node, const QVariant &value);

static mpv_node_list *allocList(int num, bool withKeys)
{
    auto *list = static_cast<mpv_node_list *>(std::calloc(1, sizeof(mpv_node_list)));
    list->num = num;
    if (num > 0) {
        list->values = static_cast<mpv_node *>(std::calloc(num, sizeof(mpv_node)));
        if (withKeys)
            list->keys = static_cast<char **>(std::calloc(num, sizeof(char *)));
    }
    return list;
}

static void buildNode(mpv_node *node, const QVariant &value)
{
    switch (value.typeId()) {
    case QMetaType::Bool:
        node->format = MPV_FORMAT_FLAG;
        node->u.flag = value.toBool() ? 1 : 0;
        return;
    case QMetaType::Int:
    case QMetaType::UInt:
    case QMetaType::LongLong:
    case QMetaType::ULongLong:
        node->format = MPV_FORMAT_INT64;
        node->u.int64 = value.toLongLong();
        return;
    case QMetaType::Float:
    case QMetaType::Double:
        node->format = MPV_FORMAT_DOUBLE;
        node->u.double_ = value.toDouble();
        return;
    case QMetaType::QString:
    case QMetaType::QByteArray:
    case QMetaType::QUrl:
        node->format = MPV_FORMAT_STRING;
        node->u.string = strdup(value.toString().toUtf8().constData());
        return;
    case QMetaType::QVariantList:
    case QMetaType::QStringList: {
        const QVariantList list = value.toList();
        node->format = MPV_FORMAT_NODE_ARRAY;
        node->u.list = allocList(int(list.size()), false);
        for (int i = 0; i < list.size(); i++)
            buildNode(&node->u.list->values[i], list.at(i));
        return;
    }
    case QMetaType::QVariantMap:
    case QMetaType::QVariantHash: {
        const QVariantMap map = value.toMap();
        node->format = MPV_FORMAT_NODE_MAP;
        node->u.list = allocList(int(map.size()), true);
        int i = 0;
        for (auto it = map.cbegin(); it != map.cend(); ++it, ++i) {
            node->u.list->keys[i] = strdup(it.key().toUtf8().constData());
            buildNode(&node->u.list->values[i], it.value());
        }
        return;
    }
    default: // invalid QVariant and unsupported types degrade to NONE
        node->format = MPV_FORMAT_NONE;
        return;
    }
}

static void freeNode(mpv_node *node)
{
    switch (node->format) {
    case MPV_FORMAT_STRING:
        std::free(node->u.string);
        break;
    case MPV_FORMAT_NODE_ARRAY:
    case MPV_FORMAT_NODE_MAP:
        for (int i = 0; i < node->u.list->num; i++) {
            if (node->u.list->keys)
                std::free(node->u.list->keys[i]);
            freeNode(&node->u.list->values[i]);
        }
        std::free(node->u.list->values);
        std::free(node->u.list->keys);
        std::free(node->u.list);
        break;
    default:
        break;
    }
}

NodeBuilder::NodeBuilder(const QVariant &value)
{
    buildNode(&node_, value);
}

NodeBuilder::~NodeBuilder()
{
    freeNode(&node_);
}

// Roundtrip check for QVariant <-> mpv_node: NodeBuilder builds the node,
// nodeToVariant reads it back, the result must equal the input.
#include "node_conv.h"

#include <QVariantList>
#include <QVariantMap>
#include <cstdio>
#include <cstdlib>

static int failures = 0;

static void check(const QVariant &in, const char *label)
{
    NodeBuilder b(in);
    QVariant out = nodeToVariant(b.node());
    if (out != in) {
        std::fprintf(stderr, "FAIL %s: in=%s out=%s\n", label,
                     qPrintable(in.toString()), qPrintable(out.toString()));
        failures++;
    }
}

int main()
{
    check(QVariant(), "none");
    check(QVariant(true), "bool true");
    check(QVariant(false), "bool false");
    check(QVariant(qint64(42)), "int64");
    check(QVariant(qint64(-7)), "int64 negative");
    check(QVariant(3.5), "double");
    check(QVariant(QStringLiteral("hello")), "string");
    check(QVariant(QStringLiteral("ñandú 東京")), "string utf8");

    // QML/JS numbers arrive as int or double; int must map to INT64.
    {
        NodeBuilder b(QVariant(7));
        QVariant out = nodeToVariant(b.node());
        if (out != QVariant(qint64(7))) {
            std::fprintf(stderr, "FAIL int promotes to int64: out=%s\n",
                         qPrintable(out.toString()));
            failures++;
        }
    }

    check(QVariant(QVariantList{qint64(1), QStringLiteral("two"), 3.5}), "list");
    check(QVariant(QVariantMap{{QStringLiteral("a"), qint64(1)},
                               {QStringLiteral("b"), true}}),
          "map");
    check(QVariant(QVariantMap{
              {QStringLiteral("cmd"),
               QVariantList{QStringLiteral("loadfile"),
                            QStringLiteral("https://x"),
                            QVariantMap{{QStringLiteral("start"), 12.5}}}}}),
          "nested map/list");
    check(QVariant(QVariantList{}), "empty list");
    check(QVariant(QVariantMap{}), "empty map");

    if (failures) {
        std::fprintf(stderr, "%d failure(s)\n", failures);
        return 1;
    }
    std::puts("node_conv roundtrip: all checks passed");
    return 0;
}

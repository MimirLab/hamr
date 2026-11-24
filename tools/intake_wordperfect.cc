#include <cstdio>
#include <stdexcept>
#include <sys/resource.h>

#include <libodfgen/OdtGenerator.hxx>
#include <librevenge-stream/librevenge-stream.h>
#include <libwpd/libwpd.h>
#include <libxml/xmlwriter.h>

namespace {
constexpr rlim_t mib = 1024 * 1024;

void constrain(int resource, rlim_t limit) {
    struct rlimit current {};
    if (getrlimit(resource, &current) != 0) {
        throw std::runtime_error("Could not inspect the converter's resource limit.");
    }
    if (current.rlim_cur > limit) {
        current.rlim_cur = limit;
        if (setrlimit(resource, &current) != 0) {
            throw std::runtime_error("Could not bound the converter's resources.");
        }
    }
}

class FlatXml final : public OdfDocumentHandler {
    xmlTextWriterPtr writer;

    static void check(int result) {
        if (result < 0) {
            throw std::runtime_error("Could not write the complete OpenDocument derivative.");
        }
    }

public:
    explicit FlatXml(const char *path) : writer(xmlNewTextWriterFilename(path, 0)) {
        if (!writer) {
            throw std::runtime_error("Could not create the OpenDocument derivative.");
        }
    }

    ~FlatXml() override { xmlFreeTextWriter(writer); }
    void startDocument() override { check(xmlTextWriterStartDocument(writer, nullptr, "UTF-8", nullptr)); }
    void endDocument() override { check(xmlTextWriterEndDocument(writer)); }

    void startElement(const char *name, const librevenge::RVNGPropertyList &attributes) override {
        check(xmlTextWriterStartElement(writer, BAD_CAST name));
        librevenge::RVNGPropertyList::Iter attribute(attributes);
        for (attribute.rewind(); attribute.next();) {
            check(xmlTextWriterWriteAttribute(writer, BAD_CAST attribute.key(),
                                              BAD_CAST attribute()->getStr().cstr()));
        }
    }

    void endElement(const char *) override { check(xmlTextWriterEndElement(writer)); }
    void characters(const librevenge::RVNGString &text) override {
        check(xmlTextWriterWriteString(writer, BAD_CAST text.cstr()));
    }
};
}

int main(int argc, char **argv) {
    if (argc != 3) {
        return 2;
    }
    try {
        constrain(RLIMIT_AS, 512 * mib);
        constrain(RLIMIT_FSIZE, 32 * mib);
        librevenge::RVNGFileStream source(argv[1]);
        const auto confidence = libwpd::WPDocument::isFileFormatSupported(&source);
        if (confidence == libwpd::WPD_CONFIDENCE_SUPPORTED_ENCRYPTION ||
            confidence == libwpd::WPD_CONFIDENCE_UNSUPPORTED_ENCRYPTION) {
            return 3;
        }
        if (confidence != libwpd::WPD_CONFIDENCE_EXCELLENT) {
            return 4;
        }
        if (source.seek(0, librevenge::RVNG_SEEK_SET)) {
            return 5;
        }
        FlatXml output(argv[2]);
        OdtGenerator generator;
        generator.addDocumentHandler(&output, ODF_FLAT_XML);
        if (libwpd::WPDocument::parse(&source, &generator, nullptr) == libwpd::WPD_OK) {
            return 0;
        }
        std::fputs("The WordPerfect reader could not decode the document body.\n", stderr);
    } catch (const std::exception &error) {
        std::fprintf(stderr, "%s\n", error.what());
    }
    return 5;
}

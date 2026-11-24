// SPDX-License-Identifier: GPL-2.0-or-later
// Separate DjVuLibre reader. It has no dependency on Hamr's Python package.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include <ddjvuapi.h>
#include <miniexp.h>

static bool messages(ddjvu_context_t *context) {
    ddjvu_message_wait(context);
    bool okay = true;
    while (const ddjvu_message_t *message = ddjvu_message_peek(context)) {
        if (message->m_any.tag == DDJVU_ERROR) {
            std::fprintf(stderr, "%s\n", message->m_error.message);
            okay = false;
        }
        ddjvu_message_pop(context);
    }
    return okay;
}

static bool output_text(miniexp_t node, int &paragraph, int &line, int depth = 0) {
    if (depth > 64 || !miniexp_consp(node)) return false;
    auto kind = miniexp_car(node);
    if (!miniexp_symbolp(kind)) return false;
    const char *tag = miniexp_to_name(kind);
    if (!std::strcmp(tag, "para")) {
        paragraph++;
        line = 0;
    }
    if (!std::strcmp(tag, "line")) line++;
    int bounds[4];
    for (int i = 0; i < 4; i++) {
        auto value = miniexp_nth(i + 1, node);
        if (!miniexp_numberp(value)) return false;
        bounds[i] = miniexp_to_int(value);
    }
    for (int i = 0; i < 5; i++) node = miniexp_cdr(node);
    for (; miniexp_consp(node); node = miniexp_cdr(node)) {
        auto child = miniexp_car(node);
        if (miniexp_stringp(child)) {
            const char *text = miniexp_to_str(child);
            if (std::strpbrk(text, "\t\r\n")) return false;
            std::printf("%d\t%d\t%d\t%d\t%d\t%d\t%s\n",
                        paragraph, line, bounds[0], bounds[1], bounds[2], bounds[3], text);
        } else if (!output_text(child, paragraph, line, depth + 1)) {
            return false;
        }
    }
    return node == miniexp_nil;
}

static bool page_image(ddjvu_context_t *context, ddjvu_document_t *document, int index) {
    auto page = ddjvu_page_create_by_pageno(document, index);
    if (!page) return false;
    bool okay = true;
    while (!ddjvu_page_decoding_done(page) && okay) okay = messages(context);
    okay = okay && !ddjvu_page_decoding_error(page);
    if (okay) {
        auto width = ddjvu_page_get_width(page);
        auto height = ddjvu_page_get_height(page);
        okay = width > 0 && height > 0 && width <= 12000000 / height;
        if (okay) {
            auto count = static_cast<size_t>(width) * height * 3;
            std::vector<char> pixels(count);
            auto format = ddjvu_format_create(DDJVU_FORMAT_RGB24, 0, nullptr);
            ddjvu_format_set_row_order(format, 1);
            ddjvu_rect_t bounds = {0, 0, static_cast<unsigned>(width), static_cast<unsigned>(height)};
            okay = ddjvu_page_render(page, DDJVU_RENDER_COLOR, &bounds, &bounds, format,
                                     width * 3, pixels.data());
            if (okay) {
                std::printf("P6\n%d %d\n255\n", width, height);
                okay = std::fwrite(pixels.data(), 1, count, stdout) == count;
            }
            ddjvu_format_release(format);
        }
    }
    ddjvu_page_release(page);
    return okay;
}

int main(int argc, char **argv) {
    if (argc != 5 || !std::freopen(argv[4], "wb", stdout)) return 2;
    char *end = nullptr;
    const long raw = std::strtol(argv[3], &end, 10);
    if (*end || raw < 0 || raw > 2000000000) return 2;
    const int page = static_cast<int>(raw);
    auto context = ddjvu_context_create("hamr-djvu");
    if (!context) return 2;
    auto document = ddjvu_document_create_by_filename_utf8(context, argv[2], 0);
    bool okay = document != nullptr;
    while (okay && !ddjvu_document_decoding_done(document)) okay = messages(context);
    okay = okay && !ddjvu_document_decoding_error(document);
    if (okay) {
        int count = ddjvu_document_get_pagenum(document);
        if (!std::strcmp(argv[1], "count") && count > 0) {
            std::printf("%d\n", count);
        } else if (page >= count) {
            okay = false;
        } else if (!std::strcmp(argv[1], "image")) {
            okay = page_image(context, document, page);
        } else if (!std::strcmp(argv[1], "text")) {
            auto text = ddjvu_document_get_pagetext(document, page, "word");
            while (text == miniexp_dummy && okay) {
                okay = messages(context);
                text = ddjvu_document_get_pagetext(document, page, "word");
            }
            if (okay && text == miniexp_nil) {
                std::puts("no-text");
            } else if (okay) {
                int width = miniexp_to_int(miniexp_nth(3, text));
                int height = miniexp_to_int(miniexp_nth(4, text));
                okay = width > 0 && height > 0;
                if (okay) {
                    std::printf("%d\t%d\n", width, height);
                    int paragraph = 1, line = 1;
                    okay = output_text(text, paragraph, line);
                }
            }
            if (text != miniexp_dummy && text != miniexp_nil)
                ddjvu_miniexp_release(document, text);
        } else {
            okay = false;
        }
    }
    if (document) ddjvu_document_release(document);
    ddjvu_context_release(context);
    return okay && !std::fflush(stdout) ? 0 : 2;
}

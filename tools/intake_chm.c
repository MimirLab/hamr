/* Read CHM records without interpreting object names as output paths. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <chm_lib.h>

enum { MAX_OBJECT = 32 * 1024 * 1024 };

struct request {
    FILE *receipt;
    FILE *selection;
    unsigned long ordinal;
    unsigned long next;
    int available;
};

static int advance(struct request *request) {
    int found = fscanf(request->selection, "%lu", &request->next);
    request->available = found == 1;
    return found == 1 || (found == EOF && feof(request->selection));
}

static int write_object(struct chmFile *book, struct chmUnitInfo *record, unsigned long ordinal) {
    if (record->length > MAX_OBJECT) return 0;
    char leaf[48];
    snprintf(leaf, sizeof leaf, "object-%lu", ordinal);
    FILE *output = fopen(leaf, "wb");
    if (!output) return 0;
    unsigned char chunk[65536];
    LONGUINT64 offset = 0;
    while (offset < record->length) {
        LONGUINT64 remaining = record->length - offset;
        LONGINT64 wanted = remaining < sizeof chunk ? remaining : sizeof chunk;
        LONGINT64 got = chm_retrieve_object(book, record, chunk, offset, wanted);
        if (got <= 0 || got > wanted || fwrite(chunk, 1, got, output) != (size_t)got) break;
        offset += got;
    }
    int closed = fclose(output);
    if (closed || offset != record->length) {
        remove(leaf);
        return 0;
    }
    return 1;
}

static int enumerate(struct chmFile *book, struct chmUnitInfo *record, void *opaque) {
    struct request *request = opaque;
    unsigned long ordinal = request->ordinal++;
    if (!request->selection) {
        long position = ftell(request->receipt);
        if (position < 0 || position >= MAX_OBJECT) {
            fputs("The CHM member index exceeds the structural memory budget.\n", stderr);
            return CHM_ENUMERATOR_FAILURE;
        }
        fprintf(request->receipt, "%lu\t%llu\t", ordinal, record->length);
        for (const unsigned char *p = (const unsigned char *)record->path; *p; ++p)
            fprintf(request->receipt, "%02x", (unsigned)*p);
        fputc('\n', request->receipt);
    } else if (request->available && ordinal == request->next) {
        fprintf(request->receipt, "%lu\t%s\n", ordinal,
                write_object(book, record, ordinal) ? "ok" : "unreadable");
        if (!advance(request) || (request->available && request->next <= ordinal))
            return CHM_ENUMERATOR_FAILURE;
        if (!request->available) return CHM_ENUMERATOR_SUCCESS;
    }
    return ferror(request->receipt) ? CHM_ENUMERATOR_FAILURE : CHM_ENUMERATOR_CONTINUE;
}

int main(int argc, char **argv) {
    int read_mode = argc == 5 && !strcmp(argv[1], "read");
    if (!read_mode && !(argc == 4 && !strcmp(argv[1], "index"))) return 2;
    struct chmFile *book = chm_open(argv[2]);
    if (!book) {
        fputs("CHMLib could not open this compiled help file.\n", stderr);
        return 1;
    }
    struct request request = {0};
    request.receipt = fopen(argv[3], "wb");
    if (read_mode) request.selection = fopen(argv[4], "rb");
    int ready = request.receipt && (!read_mode || (request.selection && advance(&request)));
    int flags = CHM_ENUMERATE_NORMAL | CHM_ENUMERATE_SPECIAL | CHM_ENUMERATE_FILES;
    int complete = ready && chm_enumerate(book, flags, enumerate, &request) && !request.available;
    if (request.receipt && fclose(request.receipt)) complete = 0;
    if (request.selection) fclose(request.selection);
    chm_close(book);
    if (!complete) fputs("CHMLib could not complete the requested records.\n", stderr);
    return complete ? 0 : 1;
}

// Host interface for the pinned, read-only Asciidoctor browser build.
let respond;
globalThis.fetch = url => new Promise(fulfill => {
    respond = fulfill;
    globalThis.hamrRequest = String(url);
});
globalThis.hamrAccept = text => {
    globalThis.hamrRequest = null;
    const found = text !== null;
    respond({ok: found, status: found ? 200 : 404, statusText: "Source unavailable",
             text: async () => text});
};

globalThis.hamrStart = async () => {
    try {
        const logger = new MemoryLogger();
        const options = {
            safe: "server", sourcemap: true, base_dir: globalThis.hamrBase,
            attributes: {docfile: globalThis.hamrFile, "allow-uri-read": ""},
            logger,
        };
        const doc = await load(globalThis.hamrInput, options);
        const nodes = [];
        const converter = doc.converter;
        const convert = converter.convert.bind(converter);
        const describe = (node, cursor, source) => ({
            path: cursor.path, file: cursor.file, line: cursor.lineno,
            context: node.getContext(), source,
        });
        converter.convert = (node, ...params) => {
            const cursor = node.getSourceLocation?.();
            if (!cursor || node.getContext() === "document") return convert(node, ...params);
            const index = nodes.length + 1;
            const description = describe(node, cursor, node.getSource?.() || node.rawTitle || "");
            if (["olist", "ulist"].includes(description.context)) {
                description.items = node.getItems().map(item => {
                    const position = item.getSourceLocation();
                    // The public getText returns the converted, rather than source, text.
                    return position ? describe(item, position, item._text) : null;
                });
            }
            nodes.push(description);
            const annotate = markup => typeof markup === "string" ?
                markup.replace(/^(\s*<[a-z][\w-]*)(?=[\s>])/,
                               '$1 data-hamr-node="' + index + '"') : markup;
            const markup = convert(node, ...params);
            return markup?.then ? markup.then(annotate) : annotate(markup);
        };
        const markup = await doc.convert({standalone: false});
        globalThis.hamrResult = JSON.stringify({
            markup, nodes, title: doc.getTitle(), lang: doc.getAttribute("lang"),
            messages: logger.getMessages().map(record => ({
                message: String(record.message),
                severity: record.severity,
                cursor: record.context?.sourceLocation ?? record.context?.["source_location"] ??
                        record.sourceLocation ?? record["source_location"],
            })),
        });
    } catch (error) {
        globalThis.hamrError = String(error);
    }
};

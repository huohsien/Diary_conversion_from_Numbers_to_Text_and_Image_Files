
(function () {
    "use strict";

    function px(value) {
        const n = parseFloat(value);
        return Number.isFinite(n) ? n : 0;
    }

    const STAMP_HEIGHT = 18;

    function fitStampToRenderedImage(fit, targetHeight) {
        const stack = fit.querySelector(".image-stamp-stack");
        const img = fit.querySelector("img");
        const strip = fit.querySelector(".image-datetime-strip");

        if (!stack || !img || !strip) return;

        // The strip is outside the image pixels but inside the inspection image
        // box. Reserve vertical space for it instead of overlaying the photo.
        const imageHeight = Math.max(1, targetHeight - STAMP_HEIGHT);
        stack.style.maxHeight = `${Math.ceil(targetHeight)}px`;
        img.style.maxHeight = `${Math.ceil(imageHeight)}px`;

        // Recalculate from the rendered image on every layout/resize. This keeps
        // the timestamp bar exactly as wide as the displayed image, not the cell.
        stack.style.width = "";
        strip.style.width = "";

        const renderedWidth = Math.ceil(img.getBoundingClientRect().width);
        if (renderedWidth <= 0) return;

        stack.style.width = `${renderedWidth}px`;
        strip.style.width = `${renderedWidth}px`;

        let fontSize = 10;
        if (renderedWidth < 155) fontSize = 9;
        if (renderedWidth < 135) fontSize = 8;
        if (renderedWidth < 115) fontSize = 7;
        if (renderedWidth < 95) fontSize = 6;
        strip.style.fontSize = `${fontSize}px`;
    }

    function layoutRows() {
        const table = document.querySelector("table");
        if (!table) return;

        const tableStyle = getComputedStyle(table);
        const basicWidth = px(tableStyle.getPropertyValue("--basic-width")) || 120;

        document.querySelectorAll("tr.diary-row").forEach((row) => {
            const textBlocks = Array.from(row.querySelectorAll(".text-value"));
            const imageFits = Array.from(row.querySelectorAll(".image-fit"));

            /*
             * Numbers-like row-height rule:
             *
             * 1. If the record has text, wrapped TEXT determines the row height.
             *    Images must fit inside that height and must NOT make the row taller.
             *
             * 2. If the record has no text and only images, fall back to one basic
             *    square-cell height as a sensible inspection height.
             *
             * This is the key behavior for rows such as 06:49: the purple text block
             * should end almost exactly at the bottom of the row, with no artificial
             * square-image baseline adding whitespace underneath it.
             */
            let targetHeight = 0;

            textBlocks.forEach((block) => {
                const cell = block.closest("td");
                if (!cell) return;

                const cellStyle = getComputedStyle(cell);
                const verticalPadding =
                    px(cellStyle.paddingTop) +
                    px(cellStyle.paddingBottom);

                const needed = block.scrollHeight + verticalPadding;
                targetHeight = Math.max(targetHeight, needed);
            });

            if (textBlocks.length === 0 && imageFits.length > 0) {
                const hasTimestampStrip = imageFits.some(
                    (fit) => fit.querySelector(".image-datetime-strip")
                );
                // Preserve the old square image baseline and add the inspection
                // strip below it instead of shrinking image-only rows.
                targetHeight = basicWidth + (hasTimestampStrip ? STAMP_HEIGHT : 0);
            }

            // Explicit/manual Numbers row heights are preserved in the canonical
            // properties CSV. Auto/default rows (20.0 in Numbers) do not carry
            // this attribute and remain purely text-driven.
            const explicitHeight = px(row.dataset.explicitRowHeight);
            if (explicitHeight > 0) {
                targetHeight = Math.max(targetHeight, explicitHeight);
            }

            const timeValue = row.querySelector(".time-value");
            if (timeValue) {
                targetHeight = Math.max(targetHeight, timeValue.scrollHeight + 8);
            }

            // Blank source rows remain compact, like Numbers.
            if (targetHeight <= 0) {
                targetHeight = 28;
            }

            row.style.height = `${Math.ceil(targetHeight)}px`;

            imageFits.forEach((fit) => {
                fit.style.height = `${Math.ceil(targetHeight)}px`;
                fitStampToRenderedImage(fit, targetHeight);
            });
        });
    }

    function scheduleLayout() {
        requestAnimationFrame(() => {
            layoutRows();
            requestAnimationFrame(layoutRows);
        });
    }

    window.addEventListener("load", scheduleLayout);
    window.addEventListener("resize", scheduleLayout);

    document.querySelectorAll(".image-fit img").forEach((img) => {
        if (!img.complete) {
            img.addEventListener("load", scheduleLayout, { once: true });
        }
    });
})();

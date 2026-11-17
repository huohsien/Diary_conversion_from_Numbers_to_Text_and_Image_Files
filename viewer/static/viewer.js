
(function () {
    "use strict";

    function px(value) {
        const n = parseFloat(value);
        return Number.isFinite(n) ? n : 0;
    }

    const STAMP_HEIGHT = 18;

    function fitStampedImageHeight(fit, targetHeight) {
        const stack = fit.querySelector(".image-stamp-stack");
        const link = fit.querySelector(".image-original-link");
        const img = fit.querySelector("img");
        const strip = fit.querySelector(".image-datetime-strip");

        if (!stack || !img || !strip) return;

        const imageHeight = Math.max(1, targetHeight - STAMP_HEIGHT);
        stack.style.maxHeight = `${Math.ceil(targetHeight)}px`;

        if (link) {
            link.style.maxHeight = `${Math.ceil(imageHeight)}px`;
        }

        img.style.maxHeight = `${Math.ceil(imageHeight)}px`;
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
                fitStampedImageHeight(fit, targetHeight);
            });
        });
    }

    let pendingLayoutFrame = null;

    function scheduleLayout() {
        if (pendingLayoutFrame !== null) {
            cancelAnimationFrame(pendingLayoutFrame);
        }

        pendingLayoutFrame = requestAnimationFrame(() => {
            pendingLayoutFrame = null;
            layoutRows();
        });
    }

    scheduleLayout();
    window.addEventListener("load", scheduleLayout);
    window.addEventListener("resize", scheduleLayout);
})();

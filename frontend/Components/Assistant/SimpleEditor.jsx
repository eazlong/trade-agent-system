"use client"; // this registers <Editor> as a Client Component
import "@blocknote/core/fonts/inter.css";
import { useCreateBlockNote } from "@blocknote/react";
import { BlockNoteView } from "@blocknote/mantine";
import "@blocknote/mantine/style.css";
import React, { useState, useEffect } from "react";

const SimpleEditor = ({ onChange=null, initialContent = null, readOnly = true }) => {
  const [content, setContent] = useState(initialContent);
  const editor = useCreateBlockNote();

  useEffect(() => {
    if (initialContent) {
      const updateEditorContent = async () => {
        const c = await editor?.tryParseHTMLToBlocks(initialContent);
        editor.replaceBlocks(editor.document, c);
        setContent(initialContent);
      };

      updateEditorContent();
    }
  }, [initialContent]);


  const handleChange = async () => {
    // Converts the editor's contents from Block objects to HTML and store to state.
    console.log("handleChange");
    const html = await editor.blocksToHTMLLossy(editor.document);
    setContent(html);
    if (onChange) {
      onChange(html);
    }
  };

  return (
      <BlockNoteView
        editor={editor}
        editable={!readOnly}
        onChange={handleChange}
      />
  );
};

export default SimpleEditor;

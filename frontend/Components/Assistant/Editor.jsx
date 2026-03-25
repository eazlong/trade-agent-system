"use client"; // this registers <Editor> as a Client Component
import "@blocknote/core/fonts/inter.css";
import {
  useCreateBlockNote,
  createReactStyleSpec,
} from "@blocknote/react";
import {
  BlockNoteSchema,
} from "@blocknote/core";

import { BlockNoteView } from "@blocknote/mantine";
import "@blocknote/mantine/style.css";
import { IconDeviceFloppy } from '@tabler/icons-react';

import React, { useState, useEffect, useCallback } from "react";
import { IMAGE_HOST } from "services";
import {
  updateEditorContent,
  uploadScreenshot,
} from "services/assistant.service";

import {
  Modal,
  Box,
  Loader,
  ActionIcon,
} from "@mantine/core";

import "@blocknote/mantine/style.css";

const small = createReactStyleSpec(
  {
    type: "small",
    propSchema: "boolean",
  },
  {
    render: (props) => {
      return <small ref={props.contentRef}></small>;
    },
  },
);

const Editor = ({contentData = null, editable=false}) => {
  const [content, setContent] = useState(contentData);
  const [changed, setChanged] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveResult, setSaveResult] = useState(null);
  const [previewImage, setPreviewImage] = useState(null);

  const schema = BlockNoteSchema.create({

    // blockSpecs: {
    //   // enable the default blocks if desired
    //   ...defaultBlockSpecs,
    //   // Add your own custom blocks:
    //   // customBlock: CustomBlock,
    // },
    // inlineContentSpecs: {
    //   // enable the default inline content if desired
    //   ...defaultInlineContentSpecs,

    //   // Add your own custom inline content:
    //   // customInlineContent: CustomInlineContent,
    // },
    // styleSpecs: {
    //   // enable the default styles if desired
    //   ...defaultStyleSpecs,

    //   // Add your own custom styles:
    //   // customStyle: CustomStyle
    //   small,
    // },
  });

  const editor = useCreateBlockNote({
    domAttributes: {
      imageBlock: {
        class: "style",
      },
    },
    schema,
  });
  
  useEffect(() => {
    if (contentData) {
      const updateEditorContent = async () => {        
        const c = await editor?.tryParseHTMLToBlocks(contentData?.content);
        editor.replaceBlocks(editor.document, c);
        setContent(contentData);
      };

      updateEditorContent();
      setChanged(false);
    }
  }, [contentData]);

  useEffect(() => {
    const autoSaveInterval = setTimeout(async () => {
      console.log(`autoSave ${changed}`);

      if (changed) {
        handleSave();
      }
    }, 10000); // 3秒自动保存一次

    return () => clearTimeout(autoSaveInterval); // 清除定时器
  }, [changed]);
  
  const handleChange = async () => {
    // Converts the editor's contents from Block objects to HTML and store to state.
    console.log("handleChange");
    const html = await editor.blocksToHTMLLossy(editor.document);
    setContent((prev) => {
      const updatedContent = { ...prev, content: html };
      return updatedContent;
    });
    setChanged(true);
  };

  const handleSave = async () => {
    try {
      setIsSaving(true);
      const htmlContent = await editor.blocksToHTMLLossy(editor.document);
        setContent((prev) => {
          const updatedContent = { ...prev, content: htmlContent };
          return updatedContent;
        });
        
        const response = await updateEditorContent(content.id, htmlContent);
        if (response.status !== 200 && response.status !== 201)
          throw new Error("保存失败");
        setChanged(false);
        setSaveResult({ type: "success", message: "内容保存成功!" });
        setTimeout(() => setSaveResult(null), 3000);
    } catch (error) {
      setSaveResult({ type: "error", message: error.message });
      setTimeout(() => setSaveResult(null), 3000);
    } finally {
      setIsSaving(false);
    }
  }

  const handlePaste = useCallback(async (event) => {
    console.log(event);
    
    const clipboardItems = await navigator.clipboard.read();
    console.log(clipboardItems);
    for (const item of clipboardItems) {
      for (const type of item.types) {
        if (type.startsWith("image/")) {
          try {
            const blob = await item.getType(type);
            const reader = new FileReader();
            reader.readAsDataURL(blob);
            reader.onloadend = async () => {
              const base64data = reader.result;
              const response = await uploadScreenshot(base64data);
              const url = IMAGE_HOST + response.data.url;
              
              const imageBlock = {
                type: "image",
                props: {
                  url: url,
                  caption: response.data.url,
                },
              };
              if (editor.getTextCursorPosition()?.block) {
                console.log("insertBlocks", editor.getTextCursorPosition().block);
                editor.insertBlocks(
                  [imageBlock],
                  editor.getTextCursorPosition().block,
                  "before"
                );
              } else {
                console.error("Unreachable case: undefined block position");
              }
            };
          } catch (error) {
            console.error("Failed to upload image:", error);
          }
        }
      }
    }
  }, [editor]);


  editor.onSelectionChange((editor) => {
    console.log("Selection changed");
    // Get current selection information
    const textCursorPosition = editor.getTextCursorPosition();
    console.log("Text cursor position:", textCursorPosition);
    if (textCursorPosition.block.type === 'image') {
      openDialogWithImage(textCursorPosition.block.props.url);
    }
  });

  const openDialogWithImage = (url) => {
    setPreviewImage(url);
  }

  return (
    <Box className="w-full h-full p-2 sm:p-3 lg:p-4 border border-purple-100 bg-purple-200 rounded-md auto-scroll">
      <Modal
        opened={!!previewImage}
        onClose={() => setPreviewImage(null)}
        centered
        size="90vw"
        withCloseButton={false}
      >
        <Box
          style={{
            position: 'relative',
            maxWidth: '100%',
            maxHeight: '90vh',
            outline: 'none',
          }}
          className="focus:outline-none"
          tabIndex={0}
          aria-label="Image preview dialog"
          onClick={() => {setPreviewImage(null)}}
          onKeyDown={(e) => {
            if (e.key === 'Escape' || e.key === 'Enter' || e.key === ' ') {
              setPreviewImage(null);
            }
          }}
        >
          <button
            type="button"
            aria-label="关闭预览"
            tabIndex={0}
            onClick={(e) => {
              e.stopPropagation();
              setPreviewImage(null);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.stopPropagation();
                setPreviewImage(null);
              }
            }}
            className="absolute top-2 right-2 z-10 bg-white bg-opacity-80 hover:bg-opacity-100 text-gray-700 rounded-full p-1 shadow focus:outline-none"
          >
            <span className="sr-only">关闭预览</span>
            <svg
              className="w-6 h-6"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              viewBox="0 0 24 24"
              aria-hidden="true"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
          <img
            src={previewImage}
            alt="Preview"
            style={{
              maxWidth: '100%',
              maxHeight: '90vh',
              objectFit: 'contain',
            }}
          />
        </Box>
      </Modal>
      <div className="flex justify-between">
        <div className="text-md text-purple-600 font-semibold px-2">{new Date(content?.created_at).toLocaleString()}</div>
        {editable && <ActionIcon
          onClick={handleSave}
          disabled={isSaving}
          // color="blue"
          size="sm"
          style={{
            minWidth: "24px",
            width: "24px",
            height: "24px",
            borderRadius: "50%",
            marginBottom: 1,
          }}
        >
          {isSaving ? <Loader size={16} /> : <IconDeviceFloppy size={16} />}
        </ActionIcon>}
      </div>
      <BlockNoteView
        editor={editor}
        sideMenu={false}
        formattingToolbar={false}
        // slashMenu={false}
        // blockTypeSelect={false}
        onPaste={handlePaste}
        // initialContent={editorContent}
        onChange={handleChange}
        editable={editable}
      />
      {saveResult && (
        <Box
          className={`mb-2 p-2 rounded ${
            saveResult.type === "success"
              ? "bg-purple-300 text-purple-800"
              : "bg-red-100 text-red-800"
          } font-size-10`}
        >
          {saveResult.message}
        </Box>
      )}
      
    </Box>
  );
};

export default Editor;

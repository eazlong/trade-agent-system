import React, { useState, useEffect } from "react";
import {
  Button,
  Modal,
  Box,
  Text,
  TextInput,
  Select,
  Grid,
  Card,
  ActionIcon,
  Badge,
  Group,
  Loader,
} from "@mantine/core";
import { IconHeart, IconSearch } from "@tabler/icons-react";
import { getEditorTemplates } from "services/assistant.service";
import Editor from "./Editor";
import { useToasts } from "react-toast-notifications";

const CATEGORIES = [
  { value: "general", label: "通用" },
  { value: "trading", label: "交易" },
  { value: "analysis", label: "分析" },
  { value: "notes", label: "笔记" },
];

const TemplateSelector = ({ open, onClose, onSelectTemplate }) => {
  const [templates, setTemplates] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState(null);

  const { addToast } = useToasts();

  useEffect(() => {
    if (open) {
      fetchTemplates();
    }
  }, [open]);

  const fetchTemplates = async () => {
    try {
      setLoading(true);
      const response = await getEditorTemplates();
      setTemplates(response.data);
    } catch (error) {
      addToast("获取模板失败: " + error.message, { appearance: "error" });
    } finally {
      setLoading(false);
    }
  };

  const handleSelectTemplate = (template) => {
    onSelectTemplate(template.content);
    onClose();
  };

  // Filter templates
  const filteredTemplates = templates.filter((template) => {
    const nameMatch = template.name.toLowerCase().includes(filter.toLowerCase());
    const categoryMatch = categoryFilter
      ? template.category === categoryFilter
      : true;
    return nameMatch && categoryMatch;
  });

  return (
    <Modal opened={open} onClose={onClose} title="选择模板" size="xl">
      {/* Filters */}
      <Group mb="md">
        <TextInput
          placeholder="搜索模板"
          value={filter}
          onChange={(event) => setFilter(event.currentTarget.value)}
          icon={<IconSearch size={14} />}
          style={{ flexGrow: 1 }}
        />
        <Select
          placeholder="分类"
          value={categoryFilter}
          onChange={setCategoryFilter}
          data={[{ value: "", label: "全部" }, ...CATEGORIES]}
          clearable
          style={{ minWidth: 120 }}
        />
      </Group>

      {/* Templates */}
      {loading ? (
        <Group position="center" py="xl">
          <Loader />
        </Group>
      ) : filteredTemplates.length > 0 ? (
        <Grid>
          {filteredTemplates.map((template) => (
            <Grid.Col span={6} key={template.id}>
              <Card
                shadow="sm"
                p="lg"
                radius="md"
                withBorder
                onClick={() => handleSelectTemplate(template)}
                style={{ cursor: "pointer" }}
              >
                <Group position="apart" mt="md" mb="xs">
                  <Text weight={500}>{template.name}</Text>
                  <ActionIcon
                    variant={template.is_favorite ? "filled" : "outline"}
                    color={template.is_favorite ? "red" : "gray"}
                    onClick={(e) => {
                      e.stopPropagation();
                      // This would be handled in the EditorTemplates component
                    }}
                  >
                    <IconHeart size={16} />
                  </ActionIcon>
                </Group>

                <Badge color="pink" variant="light" mb="sm">
                  {
                    CATEGORIES.find((c) => c.value === template.category)
                      ?.label || template.category
                  }
                </Badge>

                <Box
                  style={{
                    height: 128,
                    overflow: "hidden",
                    border: "1px solid #e9ecef",
                    borderRadius: 4,
                    padding: 8,
                  }}
                  className="auto-scroll"
                >
                  <Editor contentData={template.content} readOnly={true} />
                </Box>
              </Card>
            </Grid.Col>
          ))}
        </Grid>
      ) : (
        <Text align="center" py="xl" color="dimmed">
          没有找到匹配的模板
        </Text>
      )}
      <Group position="right" mt="md">
        <Button variant="default" onClick={onClose}>
          取消
        </Button>
        <Button
          onClick={() => window.open("/assistant/templates", "_blank")}
        >
          管理模板
        </Button>
      </Group>
    </Modal>
  );
};

export default TemplateSelector;
import React, { useState, useEffect } from "react";
import {
  Box,
  Text,
  Button,
  TextInput,
  Modal,
  Card,
  Grid,
  Divider,
  Chip,
  Tooltip,
  Group,
  Stack,
  ActionIcon,
  CardSection
} from "@mantine/core";
import {
  IconPlus,
  IconDeviceFloppy,
  IconEdit,
  IconTrash,
  IconHeart,
  IconHeartFilled,
  IconCopy,
  IconX
} from "@tabler/icons-react";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import { useToasts } from "react-toast-notifications";
import SimpleEditor from "./SimpleEditor";
import {
  getEditorTemplates,
  createEditorTemplate,
  updateEditorTemplate,
  deleteEditorTemplate
} from "services/assistant.service";

const CATEGORIES = [
  {
    value: 1,
    label: "交易系统",
    default:
      '<h2 data-level="2">xx系统 v1.0</h2><h3 data-level="3"><strong>系统目标</strong></h3><h3 data-level="3">适用市场与标的</h3><h3 data-level="3">交易风格与周期</h3><h3 data-level="3">入场规则</h3><h3 data-level="3">出场规则</h3><h3 data-level="3">仓位管理</h3><h3 data-level="3">工具与平台</h3><h3 data-level="3">风控与纪律</h3><p></p>',
  },
  {
    value: 2,
    label: "每日交易计划",
    default:
      '<h3 data-level="3">市场概况分析（宏观层面）</h3><ol><li><p>重大新闻事件</p></li><li><p>BTC/ETH趋势</p></li><li><p>美股趋势</p></li><li><p>资金流向数据</p></li></ol><h3 data-level="3"><strong>币种观察列表（Watchlist）</strong></h3><table><tr><td colspan="1" rowspan="1"><p>币种</p></td><td colspan="1" rowspan="1"><p>状态</p></td><td colspan="1" rowspan="1"><p>关注原因</p></td></tr><tr><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p></p></td></tr><tr><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p></p></td></tr></table><h3 data-level="3">风险控制与情绪预警</h3><p>1.本人情绪</p><p>2.注意事项</p><p></p>',
  },
  {
    value: 3,
    label: "币种交易计划",
    default:
      '<h3 data-level="3">技术分析标注</h3><h3 data-level="3">入场/出场点</h3><table><tr><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p>价格</p></td><td colspan="1" rowspan="1"><p>原因</p></td></tr><tr><td colspan="1" rowspan="1"><p>入场点</p></td><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p></p></td></tr><tr><td colspan="1" rowspan="1"><p>止损点</p></td><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p></p></td></tr><tr><td colspan="1" rowspan="1"><p>止盈点</p></td><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p></p></td></tr><tr><td colspan="1" rowspan="1"><p>仓位</p></td><td colspan="1" rowspan="1"><p></p></td><td colspan="1" rowspan="1"><p></p></td></tr></table><p></p>',
  },
  {
    value: 4,
    label: "交易总结",
    default:
      '<h3 data-level="3">计划执行反馈</h3><ul><li><input type="checkbox"><p class="bn-inline-content">全部按计划执行</p></li><li><input type="checkbox"><p class="bn-inline-content">部分偏离（具体见下方说明）</p></li><li><input type="checkbox"><p class="bn-inline-content">临盘冲动交易</p></li></ul><p>偏离说明：</p><h3 data-level="3">错误与偏差分析</h3><ol><li><p>情绪分析</p></li><li><p>技术分析</p></li></ol><h3 data-level="3">交易总结</h3><ol><li><p>成功点</p></li><li><p>错误改进方法</p></li></ol><p></p>',
  },
];

const EditorTemplates = () => {
  const isMobile = useMediaQuery('(max-width: 768px)');
  
  const [templates, setTemplates] = useState(
    CATEGORIES.map((x) => ({
      id: 0,
      name: x.label,
      type: x.value,
      template: x.default || "",
    }))
  );
  const [loading, setLoading] = useState(true);
  const [opened, { open, close }] = useDisclosure(false);
  const [currentTemplate, setCurrentTemplate] = useState(null);
  const [editedContent, setEditedContent] = useState("");
  const { addToast } = useToasts();
  
  // Fetch templates
  useEffect(() => {
    fetchTemplates();
  }, []);
  
  const fetchTemplates = async () => {
    try {
      setLoading(true);
      const response = await getEditorTemplates();
      setTemplates((prev) => {
        const newTemplates = prev.map(x => {
          const template = response.data.find(y => y.type === x.type);
          if (template) {
            return {
              ...x,
                template: template?.template || "",
                id: template?.id || 0
              };
          } else {  
            return x;
          }
        });
        console.log(newTemplates);
        return newTemplates;
      });
    } catch (error) {
      addToast("获取模板失败: " + error.message, {
        appearance: "error",
        autoDismiss: true,
      });
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async (template) => {
    try {
      if (template?.id) {
        await updateEditorTemplate(template.id, template);
        addToast("模板更新成功", { appearance: "success", autoDismiss: true });
      } else {
        await createEditorTemplate(template);
        addToast("模板创建成功", { appearance: "success", autoDismiss: true });
      }
      
      fetchTemplates();
      handleCloseDialog();
    } catch (error) {
      addToast("操作失败: " + error.message, { appearance: "error", autoDismiss: true });
    }
  };
  
  const handleUpdateTemplate = async (template) => {
    try {
      await updateEditorTemplate(template.id, template);
      fetchTemplates();
    } catch (error) {
      addToast("更新失败: " + error.message, { appearance: "error", autoDismiss: true });
    }
  };
  
  const handleDeleteTemplate = async (id) => {
    if (window.confirm("确定要删除此模板吗？")) {
      try {
        await deleteEditorTemplate(id);
        addToast("模板已删除", { appearance: "success", autoDismiss: true });
        fetchTemplates();
      } catch (error) {
        addToast("删除失败: " + error.message, { appearance: "error", autoDismiss: true });
      }
    }
  };
  
  const handleCopyContent = (content) => {
    navigator.clipboard.writeText(content);
    addToast("内容已复制到剪贴板", { appearance: "success", autoDismiss: true  });
  };

  const handleOpenDialog = (template) => {
    setCurrentTemplate(template);
    setEditedContent(template.template);
    open();
  };

  const handleCloseDialog = () => {
    close();
    setCurrentTemplate(null);
    setEditedContent("");
  };

  const handleSaveDialog = () => {
    if (currentTemplate) {
      const updatedTemplate = {
        ...currentTemplate,
        template: editedContent
      };
      handleSave(updatedTemplate);
    }
  };
  
  // 渲柔PC端卡片
  const renderPCCard = (template) => (
    <Card withBorder className="bg-purple-100">
      <Card.Section withBorder inheritPadding py="xs">
        <Group position="apart">
          <Text weight={700} size="lg">
            {template.name}
          </Text>
        </Group>
      </Card.Section>

      <Card.Section inheritPadding py="md">
        <Box className="h-96 overflow-hidden border rounded overflow-y-auto">
          <SimpleEditor
            initialContent={template.template}
            onChange={setEditedContent}
          />
        </Box>
      </Card.Section>

      <Card.Section withBorder inheritPadding py="xs">
        <Group position="apart" spacing="md" justify="flex-end">
          <Button
            leftSection={<IconEdit size={16} />}
            size="sm"
            variant="outline"
            onClick={() => handleOpenDialog(template)}
          >
            编辑
          </Button>
          <Button
            leftSection={<IconCopy size={16} />}
            size="sm"
            variant="outline"
            onClick={() => handleCopyContent(template.template)}
          >
            复制内容
          </Button>
        </Group>
      </Card.Section>
    </Card>
  );

  // 渲染移动端卡片
  const renderMobileCard = (template) => (
    <Card withBorder>
      <Card.Section withBorder inheritPadding py="xs">
        <Group position="apart">
          <Text weight={700} size="lg">
            {template.name}
          </Text>
        </Group>
      </Card.Section>

      <Card.Section inheritPadding py="md">
        <Box className="h-96 overflow-hidden border rounded p-2 mb-2 overflow-y-auto">
          <SimpleEditor 
            initialContent={template.template} 
            onChange={(content) => {
              template.template = content;
            }} 
          />
        </Box>
      </Card.Section>

      <Card.Section withBorder inheritPadding py="xs">
        <Group position="apart" spacing="xs">
          <Button
            leftSection={<IconDeviceFloppy size={16} />}
            size="sm"
            variant="outline"
            onClick={() => handleSave(template)}
          >
            保存
          </Button>
          <Button
            leftSection={<IconCopy size={16} />}
            size="sm"
            variant="outline"
            onClick={() => handleCopyContent(template.template)}
          >
            复制内容
          </Button>
        </Group>
      </Card.Section>
    </Card>
  );
  
  return (
    <Box className="w-full p-4">
      {/* Header */}
      <Group position="apart" mb="md">
        <Text size="xl" weight={700} c="grape">
          编辑器模板管理
        </Text>
      </Group>

      {/* Templates Grid */}
      {loading ? (
        <Text>加载中...</Text>
      ) : templates.length > 0 ? (
        <Grid gutter="md">
          {templates.map((template) => (
            <Grid.Col span={{ base: 12, md: 6, lg: 4 }} key={template.type}>
              {isMobile ? renderMobileCard(template) : renderPCCard(template)}
            </Grid.Col>
          ))}
        </Grid>
      ) : (
        <Text color="dimmed">暂无模板</Text>
      )}

      {/* 编辑模板弹窗 (PC端) */}
      {!isMobile && (
        <Modal
          opened={opened}
          onClose={handleCloseDialog}
          size="xl"
          title={`编辑 ${currentTemplate?.name} 模板`}
          centered
        >
          <Box style={{ height: "100%", overflow: "hidden"}}>
            {currentTemplate && (
              <SimpleEditor
                initialContent={currentTemplate.template}
                onChange={setEditedContent}
                readOnly={false}
              />
            )}
            <Group position="apart" spacing="xs" mt="md" justify="flex-end" p="xs">
              <Button variant="outline" onClick={handleCloseDialog}>取消</Button>
              <Button onClick={handleSaveDialog} color="blue">
                保存
              </Button>
            </Group>
          </Box>
        </Modal>
      )}
    </Box>
  );
};

export default EditorTemplates;
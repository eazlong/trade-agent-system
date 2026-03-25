import React, { useState, useEffect } from "react";
import {
  TextInput,
  Select,
  Checkbox,
  Grid,
  ActionIcon,
  Loader,
  Stack
} from "@mantine/core";
import { IconDeviceFloppy } from "@tabler/icons-react";
import { getCondition, updateCondition } from "../../services/assistant.service";

const divergenceOptions = ["大", "小"];

const ConditionCheck = ({ conditions = null }) => {
  const [isSaving, setIsSaving] = useState(false);

  const [condition, setCondition] = useState(conditions);
  
  useEffect(() => {
    if (conditions) {
      setCondition(conditions);
    }
  }, [conditions]);


  const handleSubmit = async (e) => {
    e.preventDefault();
    console.log(condition);
    if (condition?.id) {
      setIsSaving(true);
      try {
         const response = await updateCondition(condition.id, condition);
         if (response.status === 200) {
           setCondition(response.data);
         }
      } catch (error) {
        // showToast()
      } finally {
         setIsSaving(false);

      }
       
    }
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="w-full p-4 border border-gray-100 bg-purple-100 rounded-md auto-scroll"
    >
      <ActionIcon
        type="submit"
        size="sm"
        radius="xl"
        variant="filled"
        color="blue"
        loading={isSaving}
        style={{
          position: "relative",
          left: "calc(100% - 24px)",
          top: 2,
          zIndex: 1,
          marginBottom: 16
        }}
      >
        <IconDeviceFloppy size={16} />
      </ActionIcon>

      <Grid gutter="md">
        {/* 1. btc当前状态 */}
        <Grid.Col span={{ base: 12, sm: 6 }}>
          <TextInput
            label="btc当前状态"
            value={condition?.btc_status || ''}
            onChange={(e) =>
              setCondition((prev) => ({
                ...prev,
                btc_status: e.target.value,
              }))
            }
            size="sm"
          />
        </Grid.Col>
        {/* 2. 所选币种当前状态 */}
        <Grid.Col span={{ base: 12, sm: 6 }}>
          <TextInput
            label="所选币种当前状态"
            value={condition?.coin_status || ''}
            onChange={(e) =>
              setCondition((prev) => ({
                ...prev,
                coin_status: e.target.value,
              }))
            }
            size="sm"
          />
        </Grid.Col>
        {/* 3. 上方上涨空间 */}
        <Grid.Col span={{ base: 12, sm: 6 }}>
          <TextInput
            label="上方上涨空间(%)"
            type="number"
            value={condition?.upward_space || ''}
            onChange={(e) =>
              setCondition((prev) => ({
                ...prev,
                upward_space: e.target.value,
              }))
            }
            rightSection="%"
            size="sm"
          />
        </Grid.Col>
        {/* 4. 箱体调整时长 */}
        <Grid.Col span={{ base: 12, sm: 6 }}>
          <TextInput
            label="箱体调整时长"
            value={condition?.box_duration || ''}
            onChange={(e) =>
              setCondition((prev) => ({
                ...prev,
                box_duration: e.target.value,
              }))
            }
            size="sm"
          />
        </Grid.Col>
        {/* 6. 分歧判断 */}
        <Grid.Col span={{ base: 12, sm: 6 }}>
          <Select
            label="分歧判断"
            value={condition?.divergence || ''}
            onChange={(value) =>
              setCondition((prev) => ({
                ...prev,
                divergence: value,
              }))
            }
            data={divergenceOptions}
            size="sm"
          />
        </Grid.Col>
        {/* 5. 箱体是否已突破 */}
        <Grid.Col span={{ base: 12, sm: 6 }}>
          <Checkbox
            checked={condition?.box_break || false}
            onChange={(e) =>
              setCondition((prev) => ({
                ...prev,
                box_break: e.target.checked,
              }))
            }
            label="箱体是否已突破"
            size="sm"
          />
        </Grid.Col>

        {/* 7. 小周期是否出现上涨趋势 */}
        <Grid.Col span={{ base: 12, sm: 6 }}>
          <Checkbox
            checked={condition?.small_trend || false}
            onChange={(e) =>
              setCondition((prev) => ({
                ...prev,
                small_trend: e.target.checked,
              }))
            }
            label="小周期是否出现上涨趋势"
            size="sm"
          />
        </Grid.Col>
        {/* 可选：提交按钮 */}
      </Grid>
    </form>
  );
};

export default ConditionCheck;
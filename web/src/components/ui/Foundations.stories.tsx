import type { Meta, StoryObj } from "@storybook/react-vite";
import { BellIcon, CheckIcon, InfoIcon, TriangleAlertIcon } from "lucide-react";
import { Alert, AlertAction, AlertDescription, AlertTitle } from "./alert";
import { Avatar, AvatarBadge, AvatarFallback, AvatarGroup, AvatarGroupCount } from "./avatar";
import { Badge } from "./badge";
import { Button } from "./button";
import { Card, CardContent, CardHeader, CardTitle } from "./card";
import { Input } from "./input";
import { Spinner } from "./spinner";
import { Switch } from "./switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./tabs";

const buttonVariants = ["default", "secondary", "outline", "ghost", "destructive", "link"] as const;
const badgeVariants = ["default", "secondary", "outline", "ghost", "destructive", "link"] as const;
const panel = "space-y-3 border-t pt-4";

const meta = {
  title: "Foundations/Primitives",
  tags: ["visual-snapshot"],
} satisfies Meta;

export default meta;
type Story = StoryObj<typeof meta>;

export const Gallery: Story = {
  render: () => (
    <div className="w-[720px] max-w-full space-y-3 text-foreground">
      <header>
        <h1 className="text-lg font-semibold">Foundation primitives</h1>
        <p className="text-sm text-muted-foreground">Durable variants and component states.</p>
      </header>
      <div className="grid gap-3 sm:grid-cols-2">
        <section className={`${panel} sm:col-span-2`}>
          <h2 className="font-medium">Buttons</h2>
          <div className="flex flex-wrap gap-2">
            {buttonVariants.map((variant) => (
              <Button key={variant} variant={variant}>
                {variant}
              </Button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button size="xs">XS</Button>
            <Button size="sm">Small</Button>
            <Button>Default</Button>
            <Button size="lg">Large</Button>
            <Button variant="ghost" size="list">
              <Avatar>
                <AvatarFallback>DE</AvatarFallback>
              </Avatar>
              <span className="flex min-w-0 flex-col text-left">
                <span className="font-semibold">Debby</span>
                <span className="text-sm text-muted-foreground">Brainstorming partner</span>
              </span>
            </Button>
            <Button variant="outline" size="icon-sm" aria-label="Notifications">
              <BellIcon />
            </Button>
            <Button loading>Loading</Button>
            <Button disabled>Disabled</Button>
          </div>
        </section>
        <section className={panel}>
          <h2 className="font-medium">Badges</h2>
          <div className="flex flex-wrap gap-2">
            {badgeVariants.map((variant, index) => (
              <Badge key={variant} variant={variant}>
                {index === 0 && <CheckIcon data-icon="inline-start" />}
                {variant}
              </Badge>
            ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="info">
              <Spinner aria-label="Working" />
              Working
            </Badge>
            <Badge variant="warning">
              <TriangleAlertIcon />
              Needs input
            </Badge>
            <Badge variant="success">
              <CheckIcon />
              Ready
            </Badge>
            <Badge variant="destructive">
              <TriangleAlertIcon />
              Failed
            </Badge>
            <Badge variant="secondary">Offline</Badge>
          </div>
        </section>
        <section className={panel}>
          <h2 className="font-medium">Avatars</h2>
          <div className="flex items-center gap-3">
            <Avatar size="lg">
              <AvatarFallback>OM</AvatarFallback>
              <AvatarBadge>
                <CheckIcon />
              </AvatarBadge>
            </Avatar>
            <Avatar>
              <AvatarFallback>UI</AvatarFallback>
            </Avatar>
            <AvatarGroup>
              <Avatar>
                <AvatarFallback>AA</AvatarFallback>
              </Avatar>
              <Avatar>
                <AvatarFallback>BB</AvatarFallback>
              </Avatar>
              <AvatarGroupCount>+3</AvatarGroupCount>
            </AvatarGroup>
          </div>
        </section>
        <section className={panel}>
          <h2 className="font-medium">Inputs</h2>
          <Input aria-label="Default input" placeholder="Placeholder" />
          <Input aria-label="Invalid input" aria-invalid="true" defaultValue="Invalid value" />
          <Input aria-label="Disabled input" defaultValue="Disabled value" disabled />
        </section>
        <section className={panel}>
          <h2 className="font-medium">Switches</h2>
          <SwitchRow label="Checked">
            <Switch aria-label="Checked switch" defaultChecked />
          </SwitchRow>
          <SwitchRow label="Small">
            <Switch aria-label="Small switch" size="sm" />
          </SwitchRow>
          <SwitchRow label="Disabled">
            <Switch aria-label="Disabled switch" defaultChecked disabled />
          </SwitchRow>
        </section>
        <section className={`${panel} sm:col-span-2`}>
          <h2 className="font-medium">Tabs</h2>
          <div className="grid gap-4 sm:grid-cols-3">
            <TabsExample variant="default" />
            <TabsExample variant="line" />
            <TabsExample variant="pill" />
          </div>
        </section>
        <section className={`${panel} sm:col-span-2`}>
          <h2 className="font-medium">Alerts</h2>
          <div className="grid gap-2 sm:grid-cols-2">
            <Alert>
              <InfoIcon />
              <AlertTitle>Informational alert</AlertTitle>
              <AlertDescription>Title, description, icon, and action.</AlertDescription>
              <AlertAction>
                <Button variant="outline" size="xs">
                  Action
                </Button>
              </AlertAction>
            </Alert>
            <Alert variant="destructive">
              <TriangleAlertIcon />
              <AlertTitle>Destructive alert</AlertTitle>
              <AlertDescription>A concise failure using destructive tokens.</AlertDescription>
            </Alert>
          </div>
        </section>
      </div>
    </div>
  ),
};

const colorTokens = [
  ["background", "bg-background text-foreground"],
  ["card", "bg-card text-card-foreground"],
  ["muted", "bg-muted text-muted-foreground"],
  ["primary", "bg-primary text-primary-foreground"],
  ["secondary", "bg-secondary text-secondary-foreground"],
  ["accent", "bg-accent text-accent-foreground"],
] as const;

export const ThemeReference: Story = {
  render: () => (
    <div className="w-full max-w-3xl">
      <section>
        <div className="space-y-4 bg-background p-4 text-foreground">
          <h2 className="text-lg font-semibold">Theme tokens</h2>
          <div className="grid grid-cols-2 gap-2">
            {colorTokens.map(([name, classes]) => (
              <div
                key={name}
                className={`flex h-16 items-center justify-center rounded-md border text-sm ${classes}`}
              >
                {name}
              </div>
            ))}
          </div>
          <div className="space-y-2">
            <h3 className="text-base font-semibold">Section title</h3>
            <p className="text-ui">Body and control labels</p>
            <p className="text-sm text-muted-foreground">Supporting text / metadata</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Badge variant="info">
              <Spinner aria-label="Working" />
              Working
            </Badge>
            <Badge variant="warning">
              <TriangleAlertIcon />
              Waiting
            </Badge>
            <Badge variant="success">
              <CheckIcon />
              Ready
            </Badge>
            <Badge variant="destructive">
              <TriangleAlertIcon />
              Failed
            </Badge>
          </div>
          <Input aria-label="Bot name" placeholder="Bot name" />
          <div className="flex flex-wrap gap-2">
            <Button>Save</Button>
            <Button variant="outline">Cancel</Button>
            <Button disabled>Disabled</Button>
          </div>
          <Card size="sm">
            <CardHeader>
              <CardTitle>Task result</CardTitle>
            </CardHeader>
            <CardContent>Shared card surface and spacing.</CardContent>
          </Card>
        </div>
      </section>
    </div>
  ),
};

export const DarkThemeReference: Story = {
  ...ThemeReference,
  globals: { theme: "dark" },
};

function SwitchRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between rounded-md border px-2.5 py-2">
      <span className="text-ui">{label}</span>
      {children}
    </div>
  );
}

function TabsExample({ variant }: { variant: "default" | "line" | "pill" }) {
  return (
    <Tabs defaultValue="active">
      <TabsList variant={variant} aria-label={`${variant} tabs`}>
        <TabsTrigger value="active">Active</TabsTrigger>
        <TabsTrigger value="idle">Idle</TabsTrigger>
        <TabsTrigger value="disabled" disabled>
          Disabled
        </TabsTrigger>
      </TabsList>
      <TabsContent value="active" className="pt-1 text-muted-foreground">
        Active content
      </TabsContent>
    </Tabs>
  );
}

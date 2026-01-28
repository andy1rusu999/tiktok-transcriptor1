import { useEffect, useRef, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Calendar } from '@/components/ui/calendar';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Badge } from '@/components/ui/badge';
import { Progress } from '@/components/ui/progress';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { 
  Calendar as CalendarIcon, 
  Mic, 
  Languages, 
  Play, 
  Download, 
  Copy, 
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Music,
  Clock,
  User,
  FileText
} from 'lucide-react';
import { format } from 'date-fns';
import { ro } from 'date-fns/locale';
import { cn } from '@/lib/utils';
import { toast } from 'sonner';
import './App.css';

interface VideoData {
  id: string;
  url: string;
  directUrl?: string;
  title: string;
  createdAt: Date;
  duration: string;
  status: 'pending' | 'processing' | 'completed' | 'error';
  transcription?: string;
  subtitles?: string;
  subtitlesStatus?: 'idle' | 'loading' | 'completed' | 'error';
  language: string;
}

const languages = [
  { value: 'auto', label: 'Detectare automată', flag: '🌐' },
  { value: 'ru', label: 'Rusă', flag: '🇷🇺' },
  { value: 'ro', label: 'Română', flag: '🇷🇴' },
  { value: 'ro-md', label: 'Română (Moldova)', flag: '🇲🇩' },
];

const getApiBase = () => {
  const envBase = import.meta.env.VITE_API_BASE as string | undefined;
  if (envBase) {
    return envBase;
  }
  return '/api';
};
const apiBase = getApiBase();

const parseDurationToSeconds = (duration: string) => {
  const trimmed = duration.trim();
  if (!trimmed) return 0;
  if (/^\d+$/.test(trimmed)) {
    return Number(trimmed);
  }
  const parts = trimmed.split(':').map(part => Number(part));
  if (parts.some(Number.isNaN)) {
    return 0;
  }
  if (parts.length === 3) {
    const [hours, minutes, seconds] = parts;
    return hours * 3600 + minutes * 60 + seconds;
  }
  if (parts.length === 2) {
    const [minutes, seconds] = parts;
    return minutes * 60 + seconds;
  }
  return 0;
};

const formatDuration = (duration: string) => {
  const totalSeconds = parseDurationToSeconds(duration);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const labelParts = [
    hours > 0 ? `${hours} h` : null,
    minutes > 0 || hours > 0 ? `${minutes} min` : null,
    `${seconds} sec`,
  ].filter(Boolean);
  const timeParts = [
    hours > 0 ? String(hours).padStart(2, '0') : null,
    String(minutes).padStart(2, '0'),
    String(seconds).padStart(2, '0'),
  ].filter(Boolean);
  return {
    label: labelParts.join(' '),
    clock: timeParts.join(':'),
    seconds: totalSeconds,
  };
};

function App() {
  const [username, setUsername] = useState('');
  const [dateRange, setDateRange] = useState<{ from?: Date; to?: Date }>({});
  const [selectedLanguage, setSelectedLanguage] = useState('auto');
  const [isLoading, setIsLoading] = useState(false);
  const [videos, setVideos] = useState<VideoData[]>([]);
  const [overallProgress, setOverallProgress] = useState(0);
  const [activeTabById, setActiveTabById] = useState<Record<string, 'transcription' | 'subtitles'>>({});
  const [batchJobId, setBatchJobId] = useState<string | null>(null);
  const [isBatchRunning, setIsBatchRunning] = useState(false);
  const runIdRef = useRef(0);

  useEffect(() => {
    if (videos.length === 0) {
      setOverallProgress(0);
      return;
    }
    const completed = videos.filter(v => v.status === 'completed').length;
    setOverallProgress((completed / videos.length) * 100);
  }, [videos]);

  useEffect(() => {
    if (!batchJobId) return;
    let cancelled = false;
    const interval = setInterval(async () => {
      if (cancelled) return;
      try {
        const response = await fetch(`${apiBase}/job/${batchJobId}`);
        const responseText = await response.text();
        let data: any;
        try {
          data = JSON.parse(responseText);
        } catch {
          throw new Error(`Răspuns invalid de la server: ${responseText.slice(0, 200)}`);
        }
        if (!response.ok || data.error) {
          throw new Error(data?.error || `Eroare server: ${response.status}`);
        }

        setVideos(prev => prev.map(v => {
          const result = data.results?.[v.id];
          if (!result) return v;
          if (result.status === 'processing') {
            return { ...v, status: 'processing' };
          }
          if (result.status === 'completed') {
            return { ...v, status: 'completed', transcription: result.transcription };
          }
          if (result.status === 'error') {
            return { ...v, status: 'error' };
          }
          return v;
        }));

        if (data.status === 'completed') {
          setIsBatchRunning(false);
          setBatchJobId(null);
          clearInterval(interval);
        }
      } catch (err) {
        setIsBatchRunning(false);
        setBatchJobId(null);
        clearInterval(interval);
      }
    }, 2000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [batchJobId, apiBase]);

  // Încărcare videoclipuri din perioada selectată via backend
  const fetchVideos = async () => {
    // Invalidate any in-flight transcription run
    runIdRef.current += 1;
    setBatchJobId(null);
    setIsBatchRunning(false);
    let cleanUsername = username.trim();
    
    // Extrage username-ul dacă utilizatorul a introdus un link complet
    if (cleanUsername.includes('tiktok.com/')) {
      const match = cleanUsername.match(/@([^/?#]+)/);
      if (match) {
        cleanUsername = match[1];
      }
    } else if (cleanUsername.startsWith('@')) {
      cleanUsername = cleanUsername.substring(1);
    }

    if (!cleanUsername) {
      toast.error('Te rugăm să introduci numele de utilizator sau link-ul contului TikTok');
      return;
    }
    if (!dateRange.from || !dateRange.to) {
      toast.error('Te rugăm să selectezi perioada');
      return;
    }

    setIsLoading(true);
    setVideos([]); // Curăță lista veche
    setOverallProgress(0);
    setActiveTabById({});
    
    try {
      const response = await fetch(`${apiBase}/fetch-videos`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          username: cleanUsername,
          start_date: dateRange.from.toISOString(),
          end_date: dateRange.to.toISOString(),
        }),
      });

      const responseText = await response.text();
      let data: any;
      try {
        data = JSON.parse(responseText);
      } catch {
        throw new Error(`Răspuns invalid de la server: ${responseText.slice(0, 200)}`);
      }

      if (!response.ok) {
        throw new Error(data?.error || `Eroare server: ${response.status}`);
      }

      if (!Array.isArray(data.videos)) {
        toast.error('Răspuns invalid de la server.');
        setIsLoading(false);
        return;
      }

      const formattedVideos: VideoData[] = data.videos.map((v: any) => ({
        ...v,
        createdAt: new Date(v.createdAt),
        language: selectedLanguage,
      }));

      setVideos(formattedVideos);
      if (formattedVideos.length === 0) {
        toast.info('Nu am găsit videoclipuri în perioada selectată.');
      } else {
        toast.success(`${formattedVideos.length} videoclipuri găsite`);
      }
    } catch (error: any) {
      const message = error?.message || 'Nu s-a putut conecta la serverul local.';
      toast.error(message);
      console.error(error);
    } finally {
      setIsLoading(false);
    }
  };

  // Transcrierea audio reală via backend
  const transcribeVideo = async (videoId: string) => {
    const currentRunId = runIdRef.current;
    const video = videos.find(v => v.id === videoId);
    if (!video) return;

    setVideos(prev => prev.map(v => 
      v.id === videoId ? { ...v, status: 'processing' } : v
    ));

    try {
      const payload: Record<string, string> = {
        video_url: video.url,
      };
      if (video.directUrl) {
        payload.direct_url = video.directUrl;
      }
      if (selectedLanguage !== 'auto') {
        payload.language = selectedLanguage;
      }

      const response = await fetch(`${apiBase}/transcribe`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      const responseText = await response.text();
      let data: any;
      try {
        data = JSON.parse(responseText);
      } catch {
        throw new Error(`Răspuns invalid de la server: ${responseText.slice(0, 200)}`);
      }

      if (!response.ok || data.error) {
        if (runIdRef.current !== currentRunId) {
          return;
        }
        const serverError = data?.error || `Eroare server: ${response.status}`;
        toast.error(`Eroare la transcriere: ${serverError}`);
        setVideos(prev => prev.map(v => 
          v.id === videoId ? { ...v, status: 'error' } : v
        ));
        return;
      }

      if (runIdRef.current !== currentRunId) {
        return;
      }
      setVideos(prev => prev.map(v => 
        v.id === videoId ? { 
          ...v, 
          status: 'completed',
          transcription: data.transcription
        } : v
      ));

      toast.success('Transcriere finalizată!');
    } catch (error: any) {
      const message = error?.message || 'Eroare de conexiune la serverul de transcriere.';
      toast.error(message);
      if (runIdRef.current === currentRunId) {
        setVideos(prev => prev.map(v => 
          v.id === videoId ? { ...v, status: 'error' } : v
        ));
      }
    }
  };

  // Transcriere toate videoclipurile
  const transcribeAll = async () => {
    const currentRunId = runIdRef.current;
    const pendingVideos = videos.filter(v => v.status === 'pending');
    if (pendingVideos.length === 0) {
      toast.info('Nu există videoclipuri în așteptare');
      return;
    }

    setIsBatchRunning(true);

    try {
      const payload = {
        videos: pendingVideos.map(v => ({
          id: v.id,
          url: v.url,
          directUrl: v.directUrl,
          language: v.language,
        })),
      };
      const response = await fetch(`${apiBase}/transcribe-batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const responseText = await response.text();
      let data: any;
      try {
        data = JSON.parse(responseText);
      } catch {
        throw new Error(`Răspuns invalid de la server: ${responseText.slice(0, 200)}`);
      }
      if (!response.ok || data.error) {
        throw new Error(data?.error || `Eroare server: ${response.status}`);
      }
      if (runIdRef.current !== currentRunId) {
        return;
      }
      setBatchJobId(data.job_id);
    } catch (error: any) {
      toast.error(error?.message || 'Eroare la pornirea transcrierii.');
      setIsBatchRunning(false);
    }
  };


  const copyTranscription = (text: string) => {
    navigator.clipboard.writeText(text);
    toast.success('Transcriere copiată în clipboard');
  };

  const downloadTranscription = (video: VideoData) => {
    if (!video.transcription) return;
    
    const blob = new Blob([video.transcription], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcriere_${video.id}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    
    toast.success('Transcriere descărcată');
  };


  const exportCsv = () => {
    if (videos.length === 0) {
      toast.info('Nu există videoclipuri pentru export.');
      return;
    }

    const escapeCsv = (value: string | undefined) => {
      const safe = value ?? '';
      const needsQuotes = /[",\n]/.test(safe);
      const escaped = safe.replace(/"/g, '""');
      return needsQuotes ? `"${escaped}"` : escaped;
    };

    const header = ['id', 'url', 'title', 'transcription'].join(',');
    const rows = videos.map((video) => [
      escapeCsv(video.id),
      escapeCsv(video.url),
      escapeCsv(video.title),
      escapeCsv(video.transcription),
    ].join(','));

    const csvContent = [header, ...rows].join('\n');
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `tiktok_transcriptions_${Date.now()}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);

    toast.success('Export CSV generat.');
  };

  const removeVideo = (videoId: string) => {
    setVideos(prev => prev.filter(v => v.id !== videoId));
    toast.success('Videoclip eliminat');
  };


  const fetchSubtitles = async (videoId: string) => {
    const video = videos.find(v => v.id === videoId);
    if (!video || !video.url) return;

    setVideos(prev => prev.map(v =>
      v.id === videoId ? { ...v, subtitlesStatus: 'loading' } : v
    ));

    try {
      const payload: Record<string, string> = { video_url: video.url };
      if (selectedLanguage !== 'auto') {
        payload.language = selectedLanguage;
      }
      const response = await fetch(`${apiBase}/subtitles`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(payload),
      });

      const responseText = await response.text();
      let data: any;
      try {
        data = JSON.parse(responseText);
      } catch {
        throw new Error(`Răspuns invalid de la server: ${responseText.slice(0, 200)}`);
      }

      if (!response.ok || data.error) {
        const serverError = data?.error || `Eroare server: ${response.status}`;
        toast.error(`Eroare la subtitrări: ${serverError}`);
        setVideos(prev => prev.map(v =>
          v.id === videoId ? { ...v, subtitlesStatus: 'error' } : v
        ));
        return;
      }

      setVideos(prev => prev.map(v =>
        v.id === videoId ? { ...v, subtitles: data.subtitles, subtitlesStatus: 'completed' } : v
      ));
    } catch (error: any) {
      const message = error?.message || 'Eroare de conexiune la serverul de subtitrări.';
      toast.error(message);
      setVideos(prev => prev.map(v =>
        v.id === videoId ? { ...v, subtitlesStatus: 'error' } : v
      ));
    }
  };

  const getStatusIcon = (status: VideoData['status']) => {
    switch (status) {
      case 'completed':
        return <CheckCircle2 className="h-5 w-5 text-green-500" />;
      case 'processing':
        return <RefreshCw className="h-5 w-5 text-blue-500 animate-spin" />;
      case 'error':
        return <AlertCircle className="h-5 w-5 text-red-500" />;
      default:
        return <Clock className="h-5 w-5 text-gray-400" />;
    }
  };

  const getStatusBadge = (status: VideoData['status']) => {
    switch (status) {
      case 'completed':
        return <Badge variant="default" className="bg-green-500">Finalizat</Badge>;
      case 'processing':
        return <Badge variant="default" className="bg-blue-500">În procesare</Badge>;
      case 'error':
        return <Badge variant="destructive">Eroare</Badge>;
      default:
        return <Badge variant="secondary">În așteptare</Badge>;
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 p-6">
      <div className="max-w-6xl mx-auto space-y-6 relative">
        {/* Header */}
        <div className="text-center space-y-2">
          <h1 className="text-4xl font-bold text-slate-900 flex items-center justify-center gap-3">
            <Mic className="h-10 w-10 text-pink-500" />
            TikTok Audio Transcriber
          </h1>
        </div>

        {/* Configuration Card */}
        <Card className="shadow-lg">
          <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <User className="h-5 w-5" />
                Analiză cont
              </CardTitle>
            <CardDescription>
              Introdu datele contului TikTok și selectează perioada pentru transcriere
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Username Input */}
            <div className="space-y-2">
              <Label htmlFor="username">Utilizator sau Link TikTok</Label>
              <div className="flex gap-2">
                <span className="flex items-center px-3 bg-slate-100 border border-r-0 rounded-l-md text-slate-600">
                  @
                </span>
                <Input
                  id="username"
                  placeholder="nume_utilizator sau link către profil"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="rounded-l-none"
                />
              </div>
            </div>

            {/* Date Range */}
            <div className="space-y-2">
              <Label>Perioada</Label>
              <div className="flex gap-4 flex-wrap">
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      className={cn(
                        "w-[200px] justify-start text-left font-normal",
                        !dateRange.from && "text-muted-foreground"
                      )}
                    >
                      <CalendarIcon className="mr-2 h-4 w-4" />
                      {dateRange.from ? format(dateRange.from, 'PPP', { locale: ro }) : 'Data început'}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="single"
                      selected={dateRange.from}
                      onSelect={(date) => setDateRange(prev => ({ ...prev, from: date }))}
                      initialFocus
                      locale={ro}
                      className="rounded-md border bg-white shadow-sm [--cell-size:--spacing(9)]"
                      classNames={{
                        months: "gap-2",
                        month: "gap-2",
                        caption_label: "text-sm font-semibold",
                        weekdays: "mb-1",
                        weekday: "uppercase text-[0.7rem] text-slate-500 tracking-wide",
                        week: "mt-1",
                        day: "text-sm",
                        today: "bg-slate-100 text-slate-900 rounded-md",
                      }}
                    />
                  </PopoverContent>
                </Popover>

                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      variant="outline"
                      className={cn(
                        "w-[200px] justify-start text-left font-normal",
                        !dateRange.to && "text-muted-foreground"
                      )}
                    >
                      <CalendarIcon className="mr-2 h-4 w-4" />
                      {dateRange.to ? format(dateRange.to, 'PPP', { locale: ro }) : 'Data sfârșit'}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent className="w-auto p-0" align="start">
                    <Calendar
                      mode="single"
                      selected={dateRange.to}
                      onSelect={(date) => setDateRange(prev => ({ ...prev, to: date }))}
                      initialFocus
                      locale={ro}
                      className="rounded-md border bg-white shadow-sm [--cell-size:--spacing(9)]"
                      classNames={{
                        months: "gap-2",
                        month: "gap-2",
                        caption_label: "text-sm font-semibold",
                        weekdays: "mb-1",
                        weekday: "uppercase text-[0.7rem] text-slate-500 tracking-wide",
                        week: "mt-1",
                        day: "text-sm",
                        today: "bg-slate-100 text-slate-900 rounded-md",
                      }}
                    />
                  </PopoverContent>
                </Popover>
              </div>
            </div>

            {/* Language Selection */}
            <div className="space-y-2">
              <Label htmlFor="language" className="flex items-center gap-2">
                <Languages className="h-4 w-4" />
                Limba pentru transcriere
              </Label>
              <Select value={selectedLanguage} onValueChange={setSelectedLanguage}>
                <SelectTrigger className="w-full">
                  <SelectValue placeholder="Selectează limba" />
                </SelectTrigger>
                <SelectContent>
                  {languages.map((lang) => (
                    <SelectItem key={lang.value} value={lang.value}>
                      <span className="flex items-center gap-2">
                        <span>{lang.flag}</span>
                        <span>{lang.label}</span>
                      </span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-sm text-slate-500">
                {selectedLanguage === 'ro-md' && 'Include suport pentru slang moldovenesc și rusisme'}
              </p>
            </div>

            {/* Fetch Button */}
            <Button 
              onClick={fetchVideos} 
              disabled={isLoading}
              className="w-full bg-gradient-to-r from-pink-500 to-purple-600 hover:from-pink-600 hover:to-purple-700"
            >
              {isLoading ? (
                <>
                  <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                  Se încarcă videoclipurile...
                </>
              ) : (
                <>
                  <Music className="mr-2 h-4 w-4" />
                  Încarcă videoclipuri
                </>
              )}
            </Button>
          </CardContent>
        </Card>

        {/* Progress Overview */}
        {videos.length > 0 && (
          <Card className="shadow-lg">
            <CardHeader>
              <CardTitle className="flex items-center justify-between">
                <span className="flex items-center gap-2">
                  <FileText className="h-5 w-5" />
                  Progres General
                </span>
                <span className="text-2xl font-bold text-slate-700">
                  {Math.round(overallProgress)}%
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Progress
                value={overallProgress}
                className="h-3"
              />
              <div className="flex justify-between mt-2 text-sm text-slate-600">
                <span>{videos.filter(v => v.status === 'completed').length} finalizate</span>
                <span>{videos.filter(v => v.status === 'pending').length} în așteptare</span>
                <span>{videos.filter(v => v.status === 'processing').length} în procesare</span>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Videos List */}
        {videos.length > 0 && (
          <Card className="shadow-lg">
            <CardHeader>
              <div className="flex items-center justify-between">
                <div>
                  <CardTitle className="flex items-center gap-2">
                    <Music className="h-5 w-5" />
                    Videoclipuri Găsite
                  </CardTitle>
                  <CardDescription>
                    {videos.length} videoclipuri în perioada selectată
                  </CardDescription>
                </div>
                <div className="flex items-center gap-2">
                  <Button 
                    onClick={transcribeAll}
                    disabled={videos.every(v => v.status !== 'pending') || isBatchRunning}
                    className="bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700"
                  >
                    <Play className="mr-2 h-4 w-4" />
                    {isBatchRunning ? 'Se transcrie...' : 'Transcrie toate'}
                  </Button>
                  <Button 
                    onClick={exportCsv}
                    variant="outline"
                  >
                    Export CSV
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              <ScrollArea className="h-[500px]">
                <div className="space-y-4">
                  {videos.map((video) => (
                    <div key={video.id}>
                      <div className="flex items-start justify-between p-4 bg-slate-50 rounded-lg">
                        <div className="flex-1 space-y-2">
                          <div className="flex items-center gap-3">
                            {getStatusIcon(video.status)}
                            <span className="font-medium">{video.title}</span>
                            {getStatusBadge(video.status)}
                          </div>
                          <div className="flex items-center gap-4 text-sm text-slate-500">
                            {(() => {
                              const durationInfo = formatDuration(video.duration);
                              return (
                                <span className="flex items-center gap-1">
                                  <Clock className="h-4 w-4" />
                                  Durata: {durationInfo.clock}
                                </span>
                              );
                            })()}
                            <span className="flex items-center gap-1">
                              Data: {format(video.createdAt, 'dd MMM yyyy', { locale: ro })}
                            </span>
                            <span className="flex items-center gap-1">
                              <Languages className="h-4 w-4" />
                              Limbă: {languages.find(l => l.value === video.language)?.label}
                            </span>
                          </div>
                          
                          <Tabs
                            value={activeTabById[video.id] || 'transcription'}
                            onValueChange={(value) => {
                              const tab = value as 'transcription' | 'subtitles';
                              setActiveTabById(prev => ({ ...prev, [video.id]: tab }));
                              if (tab === 'subtitles' && !video.subtitles && video.subtitlesStatus !== 'loading') {
                                fetchSubtitles(video.id);
                              }
                            }}
                            className="mt-3"
                          >
                            <TabsList>
                              <TabsTrigger value="transcription">Transcriere</TabsTrigger>
                              <TabsTrigger value="subtitles">Subtitrări</TabsTrigger>
                            </TabsList>
                            <TabsContent value="transcription" className="mt-3">
                              <div className="p-3 bg-white border rounded-md">
                                <p className="text-sm text-slate-700 whitespace-pre-wrap">
                                  {video.transcription || 'Transcrierea nu este disponibilă încă.'}
                                </p>
                              </div>
                            </TabsContent>
                            <TabsContent value="subtitles" className="mt-3">
                              <div className="p-3 bg-white border rounded-md">
                                <p className="text-sm text-slate-700 whitespace-pre-wrap">
                                  {video.subtitlesStatus === 'loading'
                                    ? 'Se încarcă subtitrările...'
                                    : video.subtitles || 'Subtitrări indisponibile pentru acest clip.'}
                                </p>
                              </div>
                            </TabsContent>
                          </Tabs>
                        </div>

                        <div className="flex items-center gap-2 ml-4">
                          {video.status === 'pending' && (
                            <Button
                              size="sm"
                              onClick={() => transcribeVideo(video.id)}
                              variant="outline"
                            >
                              <Play className="h-4 w-4" />
                            </Button>
                          )}
                          {video.transcription && (
                            <>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => copyTranscription(video.transcription!)}
                              >
                                <Copy className="h-4 w-4" />
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                onClick={() => downloadTranscription(video)}
                              >
                                <Download className="h-4 w-4" />
                              </Button>
                            </>
                          )}
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => removeVideo(video.id)}
                            className="text-red-500 hover:text-red-700 hover:bg-red-50"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      </div>
                      <Separator className="my-2" />
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        )}

      </div>
    </div>
  );
}

export default App;
